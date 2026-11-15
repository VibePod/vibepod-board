"""Per-area token permissions, enforced over REST and over MCP."""

import json
from typing import Any

import pytest
from conftest import call, login
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlmodel import Session

from vibepod_board import permissions
from vibepod_board.access import admin_access
from vibepod_board.api.system import MCP_INFO_TOOLS
from vibepod_board.auth import require_permitted_access
from vibepod_board.permissions import FULL_ACCESS, Area
from vibepod_board.schemas import Project
from vibepod_board.services import ideas, projects, tokens

ADMIN = admin_access("admin")
NO_ACCESS = dict.fromkeys(FULL_ACCESS, "none")
STATE = "vibepod-board://state"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def project(session: Session) -> Project:
    created = projects.create_project(session, "APP", "App")
    ideas.create_idea(session, ADMIN, title="Existing task", project_id=created.id)
    return created


def token_with(session: Session, project: Project, /, **granted: str) -> str:
    """A token for `project` with exactly the named levels; every other area has none."""
    return tokens.create_token(session, "Agent", [project.id], permissions=granted).token


# REST


def test_a_read_only_token_reads_tasks_but_cannot_change_them(
    client: TestClient, session: Session, project: Project
) -> None:
    headers = bearer(token_with(session, project, tasks="read"))

    listed = client.get("/api/ideas", headers=headers)
    assert listed.status_code == 200
    assert [idea["title"] for idea in listed.json()["items"]] == ["Existing task"]

    created = client.post("/api/ideas", headers=headers, json={"title": "New"})
    assert created.status_code == 403
    assert created.json() == {"error": "This token has no write access to Tasks"}

    idea_id = listed.json()["items"][0]["id"]
    assert client.get(f"/api/ideas/{idea_id}", headers=headers).status_code == 200
    changed = client.patch(f"/api/ideas/{idea_id}", headers=headers, json={"title": "Renamed"})
    assert changed.status_code == 403
    assert changed.json() == {"error": "This token has no write access to Tasks"}


def test_write_access_covers_reading(
    client: TestClient, session: Session, project: Project
) -> None:
    headers = bearer(token_with(session, project, tasks="write"))

    created = client.post("/api/ideas", headers=headers, json={"title": "New"})
    assert created.status_code == 201
    assert client.get("/api/ideas", headers=headers).status_code == 200
    idea_id = created.json()["item"]["id"]
    renamed = client.patch(f"/api/ideas/{idea_id}", headers=headers, json={"title": "Renamed"})
    assert renamed.status_code == 200


def test_a_token_cannot_reach_an_area_it_has_no_access_to(
    client: TestClient, session: Session, project: Project
) -> None:
    headers = bearer(token_with(session, project, tasks="write"))

    board = client.get("/api/board", headers=headers)
    assert board.status_code == 403
    assert board.json() == {"error": "This token has no read access to Board"}
    documents = client.get("/api/documents", headers=headers)
    assert documents.json() == {"error": "This token has no read access to Documents"}
    workers = client.get("/api/workers", headers=headers)
    assert workers.json() == {"error": "This token has no read access to Automation"}


def test_every_token_may_list_its_projects(
    client: TestClient, session: Session, project: Project
) -> None:
    headers = bearer(tokens.create_token(session, "Agent", [project.id], NO_ACCESS).token)

    listed = client.get("/api/projects", headers=headers)

    assert listed.status_code == 200
    assert [item["key"] for item in listed.json()["items"]] == ["APP"]


def test_a_token_without_chosen_permissions_is_not_restricted(
    client: TestClient, session: Session, project: Project
) -> None:
    headers = bearer(tokens.create_token(session, "Agent", [project.id]).token)

    assert client.post("/api/ideas", headers=headers, json={"title": "New"}).status_code == 201
    assert client.get("/api/board", headers=headers).status_code == 200
    assert client.get("/api/documents", headers=headers).status_code == 200


def test_admin_only_routes_still_ask_a_token_for_the_admin(
    client: TestClient, session: Session, project: Project
) -> None:
    headers = bearer(token_with(session, project, project="write"))

    response = client.post("/api/projects", headers=headers, json={"key": "NEW", "title": "No"})

    assert response.status_code == 403
    assert response.json() == {"error": "Admin access required"}


def test_the_admin_is_not_restricted(client: TestClient, project: Project) -> None:
    admin = login(client)

    assert admin.get("/api/board").status_code == 200
    assert admin.post("/api/ideas", json={"title": "New"}).status_code == 201


def _depends_on(dependant: Dependant, call: Any) -> bool:
    return dependant.call is call or any(
        _depends_on(child, call) for child in dependant.dependencies
    )


def _api_routes(routes: Any) -> list[APIRoute]:
    """Every API route, including those of the routers the app includes."""
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif hasattr(route, "original_router"):
            found.extend(_api_routes(route.original_router.routes))
    return found


def test_every_route_a_token_can_reach_names_its_permission(client: TestClient) -> None:
    reachable = {
        f"{method} {route.path}"
        for route in _api_routes(client.app.routes)  # type: ignore[attr-defined]
        if _depends_on(route.dependant, require_permitted_access)
        for method in route.methods
    }

    assert reachable == set(permissions.ROUTE_PERMISSIONS)


def test_every_mcp_tool_names_its_permission() -> None:
    assert set(permissions.TOOL_PERMISSIONS) == set(MCP_INFO_TOOLS)


# Managing permissions


def test_the_api_creates_lists_and_changes_token_permissions(
    client: TestClient, project: Project
) -> None:
    admin = login(client)

    created = admin.post(
        "/api/tokens",
        json={"name": "Planner", "projectIds": [project.id], "permissions": {"tasks": "write"}},
    )
    assert created.status_code == 201
    token_id = created.json()["item"]["id"]
    assert created.json()["item"]["permissions"] == {**NO_ACCESS, "tasks": "write"}
    assert admin.get("/api/tokens").json()["items"][0]["permissions"] == {
        **NO_ACCESS,
        "tasks": "write",
    }

    changed = admin.patch(f"/api/tokens/{token_id}", json={"permissions": {"board": "read"}})
    assert changed.status_code == 200
    assert changed.json()["item"]["permissions"] == {**NO_ACCESS, "board": "read"}

    default = admin.post("/api/tokens", json={"name": "Full", "projectIds": [project.id]})
    assert default.json()["item"]["permissions"] == FULL_ACCESS


def test_the_api_refuses_permissions_it_does_not_know(client: TestClient, project: Project) -> None:
    admin = login(client)
    body = {"name": "Agent", "projectIds": [project.id]}

    area = admin.post("/api/tokens", json={**body, "permissions": {"billing": "read"}})
    level = admin.post("/api/tokens", json={**body, "permissions": {"tasks": "admin"}})

    assert area.status_code == 400
    assert area.json() == {"error": "Unknown permission area: billing"}
    assert level.status_code == 400
    assert level.json() == {"error": "Unknown permission level for tasks: admin"}


def test_lists_the_permission_areas_without_a_login(client: TestClient) -> None:
    response = client.get("/api/permission-areas")

    assert response.status_code == 200
    areas = {area["key"]: area for area in response.json()["items"]}
    assert list(areas) == [area.value for area in Area]
    assert areas["tasks"]["label"] == "Tasks"
    assert areas["tasks"]["levels"] == ["none", "read", "write"]
    assert areas["project"]["levels"] == ["none", "write"]
    assert "list_ideas" in areas["tasks"]["readTools"]
    assert "create_idea" in areas["tasks"]["writeTools"]


# MCP


async def test_mcp_lists_only_the_tools_a_token_may_call(
    server_url: str, session: Session, project: Project
) -> None:
    token = token_with(session, project, tasks="read", documents="write")

    async with Client(server_url, auth=token) as client:
        names = {tool.name for tool in await client.list_tools()}

    tasks, documents = permissions.POLICY[Area.TASKS], permissions.POLICY[Area.DOCUMENTS]
    assert names == {
        *permissions.OPEN_TOOLS,
        *tasks.read_tools,
        *documents.read_tools,
        *documents.write_tools,
    }


async def test_mcp_lists_every_tool_for_a_token_with_full_access(
    server_url: str, session: Session, project: Project
) -> None:
    token = tokens.create_token(session, "Agent", [project.id]).token

    async with Client(server_url, auth=token) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names == set(MCP_INFO_TOOLS)


async def test_mcp_runs_the_tools_a_token_may_call_and_refuses_the_others(
    server_url: str, session: Session, project: Project
) -> None:
    token = token_with(session, project, tasks="read")

    listed = await call(server_url, token, "list_ideas")
    assert [idea["title"] for idea in listed["items"]] == ["Existing task"]

    with pytest.raises(ToolError, match="This token has no write access to Tasks"):
        await call(server_url, token, "create_idea", title="New")
    with pytest.raises(ToolError, match="This token has no read access to Board"):
        await call(server_url, token, "list_board")


async def test_mcp_state_resource_needs_read_access_to_everything_it_shows(
    server_url: str, session: Session, project: Project
) -> None:
    partial = token_with(session, project, tasks="read", board="read")
    reader = token_with(session, project, tasks="read", board="read", documents="read")

    async with Client(server_url, auth=partial) as client:
        assert await client.list_resources() == []
        with pytest.raises(Exception, match="This token has no read access to Documents"):
            await client.read_resource(STATE)

    async with Client(server_url, auth=reader) as client:
        assert [str(resource.uri) for resource in await client.list_resources()] == [STATE]
        state = json.loads((await client.read_resource(STATE))[0].text)
    assert [idea["title"] for idea in state["ideas"]] == ["Existing task"]
