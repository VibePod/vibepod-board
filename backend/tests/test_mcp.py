"""MCP over real HTTP: a uvicorn server and a FastMCP client authenticating with a board token."""

import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.config import Settings
from vibepod_board.main import create_app
from vibepod_board.services import board, ideas, projects, tokens

ADMIN = admin_access("admin")


@pytest.fixture
def server_url(db: Engine, settings: Settings) -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(
        create_app(settings, run_migrations=False), host="127.0.0.1", port=port, log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def scoped(session: Session) -> dict[str, Any]:
    """Two projects with one ready task each, and a token mapped to APP only."""
    app = projects.create_project(session, "APP", "App")
    api = projects.create_project(session, "API", "API")
    mine = ideas.create_idea(session, ADMIN, title="Visible", project_id=app.id)
    theirs = ideas.create_idea(session, ADMIN, title="Hidden", project_id=api.id)
    ideas.mark_ready(session, ADMIN, mine.id)
    ideas.mark_ready(session, ADMIN, theirs.id)
    token = tokens.create_token(session, "Agent", [app.id]).token
    return {
        "app": app,
        "api": api,
        "mine": mine,
        "theirs": theirs,
        "mine_card": board.board_columns(session, ADMIN, app.id).ready[0],
        "their_card": board.board_columns(session, ADMIN, api.id).ready[0],
        "token": token,
    }


async def call(url: str, token: str, tool: str, **arguments: Any) -> Any:
    async with Client(url, auth=token) as client:
        result = await client.call_tool(tool, arguments)
        return json.loads(result.content[0].text)


async def post_raw(url: str, token: str | None = None) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    async with httpx.AsyncClient() as http:
        return (await http.post(url, json=request, headers=headers)).status_code


async def test_requires_a_valid_bearer_token(server_url: str, scoped: dict[str, Any]) -> None:
    assert await post_raw(server_url) == 401
    assert await post_raw(server_url, "invalid") == 401


async def test_revoked_tokens_are_rejected(
    server_url: str, scoped: dict[str, Any], session: Session
) -> None:
    token_id = tokens.list_tokens(session)[0].id
    tokens.revoke_token(session, token_id)
    assert await post_raw(server_url, scoped["token"]) == 401


async def test_lists_every_tool_and_the_state_resource(
    server_url: str, scoped: dict[str, Any]
) -> None:
    async with Client(server_url, auth=scoped["token"]) as client:
        names = {tool.name for tool in await client.list_tools()}
        resources = [str(resource.uri) for resource in await client.list_resources()]
        state = json.loads((await client.read_resource("vibepod-board://state"))[0].text)

    assert names == {
        "list_projects",
        "create_project",
        "list_ideas",
        "create_idea",
        "update_idea",
        "add_idea_dependency",
        "remove_idea_dependency",
        "set_idea_dependencies",
        "list_work_order",
        "mark_idea_ready",
        "list_board",
        "move_board_card",
        "update_board_card",
        "set_card_readiness",
        "set_idea_readiness",
        "list_idea_readiness",
        "create_document",
        "update_document",
        "list_documents",
        "push_github_issue",
        "pull_github_issue",
        "upsert_github_issue",
        "delete_idea",
    }
    assert resources == ["vibepod-board://state"]
    assert [project["key"] for project in state["projects"]] == ["APP"]
    assert state["activity"] == []


async def test_scopes_tool_calls_to_mapped_projects(
    server_url: str, scoped: dict[str, Any]
) -> None:
    url, token = server_url, scoped["token"]

    with pytest.raises(ToolError, match="Admin access required"):
        await call(url, token, "create_project", key="NEW", title="Nope")
    with pytest.raises(ToolError, match="Token is not allowed to access project"):
        await call(url, token, "create_idea", projectId=scoped["api"].id, title="Forbidden")

    listed = await call(url, token, "list_ideas")
    assert [item["title"] for item in listed["items"]] == ["Visible"]

    created = await call(url, token, "create_idea", title="Defaults to the mapped project")
    assert created["item"]["projectId"] == scoped["app"].id


async def test_edits_ideas_in_mapped_projects_only(server_url: str, scoped: dict[str, Any]) -> None:
    url, token = server_url, scoped["token"]
    updated = await call(
        url,
        token,
        "update_idea",
        id=scoped["mine"].id,
        title="Edited title",
        labels=["enhancement"],
    )
    assert updated["item"]["title"] == "Edited title"
    assert updated["item"]["labels"] == ["enhancement"]

    with pytest.raises(ToolError, match="Token is not allowed to access project"):
        await call(url, token, "update_idea", id=scoped["theirs"].id, title="Nope")


async def test_updates_board_card_metadata_in_mapped_projects(
    server_url: str, scoped: dict[str, Any]
) -> None:
    url, token = server_url, scoped["token"]
    updated = await call(
        url,
        token,
        "update_board_card",
        id=scoped["mine_card"].id,
        branchName="vp-task-create",
        details="Implemented in vibepod-cli",
        repositoryLocalPath="/workspace/vibepod-cli",
        repositoryRemoteUrl="git@github.com:vibepod/vibepod-cli.git",
    )
    assert {
        key: updated["item"][key]
        for key in ("id", "branchName", "details", "repositoryLocalPath", "repositoryRemoteUrl")
    } == {
        "id": scoped["mine_card"].id,
        "branchName": "vp-task-create",
        "details": "Implemented in vibepod-cli",
        "repositoryLocalPath": "/workspace/vibepod-cli",
        "repositoryRemoteUrl": "git@github.com:vibepod/vibepod-cli.git",
    }

    with pytest.raises(ToolError, match="Token is not allowed to access project"):
        await call(
            url, token, "update_board_card", id=scoped["their_card"].id, branchName="vp-task-cancel"
        )


async def test_sets_card_readiness_in_mapped_projects_only(
    server_url: str, scoped: dict[str, Any]
) -> None:
    url, token = server_url, scoped["token"]
    scored = await call(
        url,
        token,
        "set_card_readiness",
        id=scoped["mine_card"].id,
        score=3,
        reason="No acceptance criteria, repository unset",
    )
    assert scored["item"]["readinessScore"] == 3
    assert scored["item"]["readinessReason"] == "No acceptance criteria, repository unset"

    with pytest.raises(ToolError, match="Token is not allowed to access project"):
        await call(
            url, token, "set_card_readiness", id=scoped["their_card"].id, score=5, reason="r"
        )


async def test_runs_the_dependency_workflow(server_url: str, scoped: dict[str, Any]) -> None:
    url, token = server_url, scoped["token"]
    blocker = (await call(url, token, "create_idea", title="Schema"))["item"]
    task = scoped["mine"]

    linked = await call(url, token, "add_idea_dependency", id=task.id, dependsOnId=blocker["id"])
    assert linked["item"]["dependsOn"] == [blocker["id"]]
    assert linked["item"]["blockedBy"] == [blocker["id"]]

    order = await call(url, token, "list_work_order", projectId=scoped["app"].id)
    assert [item["title"] for item in order["items"]] == ["Schema", "Visible"]
    assert [item["isActionable"] for item in order["items"]] == [True, False]

    with pytest.raises(ToolError, match="Dependency cycle detected"):
        await call(url, token, "add_idea_dependency", id=blocker["id"], dependsOnId=task.id)

    cleared = await call(url, token, "set_idea_dependencies", id=task.id, dependsOnIds=[])
    assert cleared["item"]["dependsOn"] == []

    board_state = await call(url, token, "list_board", projectId=scoped["app"].id)
    card_id = board_state["columns"]["ready"][0]["id"]
    moved = await call(url, token, "move_board_card", id=card_id, column="done")
    assert moved["item"]["column"] == "done"


async def test_deletes_tasks_in_mapped_projects_only(
    server_url: str, scoped: dict[str, Any]
) -> None:
    url, token = server_url, scoped["token"]
    with pytest.raises(ToolError, match="Token is not allowed to access project"):
        await call(url, token, "delete_idea", id=scoped["theirs"].id)

    deleted = await call(url, token, "delete_idea", id=scoped["mine"].id)
    assert deleted == {"id": scoped["mine"].id, "taskId": "APP-1", "dependents": []}
    assert (await call(url, token, "list_ideas"))["items"] == []
