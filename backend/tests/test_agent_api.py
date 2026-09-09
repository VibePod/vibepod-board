"""The agent-facing API over REST and MCP: single reads by key, views, filters, paging,
batches, inline readiness and project readiness."""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import call, login
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.api.system import MCP_INFO_TOOLS
from vibepod_board.services import documents, ideas, projects, readiness, tokens

ADMIN = admin_access("admin")
HOLDER = "Claude::Subagent101::Worktree12"
README = Path(__file__).resolve().parents[2] / "README.md"


# --- REST ----------------------------------------------------------------------------


@pytest.fixture
def api(client: TestClient) -> TestClient:
    login(client)
    assert client.post("/api/projects", json={"key": "VP", "title": "VibePod"}).status_code == 201
    return client


def create(client: TestClient, title: str, **fields: Any) -> dict[str, Any]:
    response = client.post("/api/ideas", json={"projectId": "VP", "title": title, **fields})
    assert response.status_code == 201, response.text
    return response.json()["item"]


def test_rest_reads_one_task_and_one_card_by_key(api: TestClient) -> None:
    task = create(api, "Readable")
    api.post(f"/api/ideas/{task['id']}/ready")

    one = api.get("/api/ideas/VP-1").json()["item"]
    assert (one["id"], one["title"], one["key"]) == (task["id"], "Readable", "VP-1")
    card = api.get("/api/board/vp-1").json()["item"]
    assert (card["ideaId"], card["column"], card["key"]) == (task["id"], "ready", "VP-1")
    assert set(api.get("/api/ideas/VP-1", params={"view": "ref"}).json()["item"]) == {
        "id",
        "key",
        "updatedAt",
    }

    # The longer routes still win over the single-record ones.
    assert api.get(f"/api/ideas/{task['id']}/readiness").json() == {"items": []}
    assert api.get("/api/board/archived").json() == {"items": []}
    assert api.get("/api/ideas/VP-999").status_code == 404
    assert api.get("/api/board/VP-999").status_code == 404
    assert api.get("/api/ideas/VP-1", params={"view": "tiny"}).status_code == 400


def test_rest_filters_task_and_board_lists(api: TestClient) -> None:
    ready = create(api, "Ready to go")
    create(api, "Still an idea")
    api.post(f"/api/ideas/{ready['id']}/ready")

    ready_only = api.get("/api/ideas", params={"projectId": "VP", "status": "ready"}).json()
    assert [idea["id"] for idea in ready_only["items"]] == [ready["id"]]

    columns = api.get("/api/board", params={"column": "ready"}).json()["columns"]
    assert len(columns["ready"]) == 1
    assert columns["planned"] == []
    assert api.get("/api/board", params={"column": "planned"}).json()["columns"]["ready"] == []

    future = "2999-01-01T00:00:00.000Z"
    assert api.get("/api/ideas", params={"updatedSince": future}).json()["items"] == []


def test_rest_keeps_full_tasks_by_default(api: TestClient) -> None:
    create(api, "Full by default", details="The browser builds its edit form from this")
    full = api.get("/api/ideas").json()["items"][0]
    assert full["details"] == "The browser builds its edit form from this"
    compact = api.get("/api/ideas", params={"view": "compact"}).json()["items"][0]
    assert "details" not in compact
    assert compact["key"] == "VP-1"


def test_rest_accepts_a_project_key_and_rejects_an_ambiguous_reference(api: TestClient) -> None:
    create(api, "Filed under a key")
    assert len(api.get("/api/ideas", params={"projectId": "VP"}).json()["items"]) == 1
    api.post("/api/projects", json={"key": "BRD", "title": "Board"})
    api.post("/api/projects", json={"key": "BDX", "title": "Board"})

    ambiguous = api.get("/api/ideas", params={"projectId": "Board"})
    assert ambiguous.status_code == 400
    assert "Ambiguous project reference" in ambiguous.json()["error"]
    assert api.get("/api/ideas", params={"projectId": "Nope"}).status_code == 404


def test_rest_lists_readiness_for_a_whole_project(api: TestClient) -> None:
    rated = create(api, "Rated")
    create(api, "Never rated")
    api.post(f"/api/ideas/{rated['id']}/readiness", json={"score": 4, "reason": "Needs criteria"})
    api.post(f"/api/ideas/{rated['id']}/readiness", json={"score": 6, "reason": "Better"})

    latest = api.get("/api/readiness", params={"projectId": "VP"}).json()["items"]
    assert [(event["ideaId"], event["score"]) for event in latest] == [(rated["id"], 6)]
    history = api.get("/api/readiness", params={"tasks": "VP-1,VP-2", "latestOnly": "false"})
    assert [event["score"] for event in history.json()["items"]] == [6, 4]


def test_rest_writes_board_membership_and_readiness_with_the_task(api: TestClient) -> None:
    task = create(api, "Collapsed")
    patched = api.patch(
        "/api/ideas/VP-1",
        json={"onBoard": True, "readiness": {"score": 7, "reason": "Clear"}},
    )
    assert patched.status_code == 200
    assert patched.json()["item"]["readinessScore"] == 7
    assert api.get("/api/board/VP-1").json()["item"]["readinessScore"] == 7

    assert api.patch("/api/ideas/VP-1", json={"onBoard": False}).status_code == 200
    assert api.get("/api/board/VP-1").status_code == 404

    ready = api.post(
        f"/api/ideas/{task['id']}/ready", json={"readiness": {"score": 9, "reason": "Go"}}
    )
    assert ready.json()["item"]["readinessScore"] == 9
    bad = api.patch("/api/ideas/VP-1", json={"readiness": {"score": 0, "reason": "x"}})
    assert bad.status_code == 400


def test_mcp_info_advertises_every_registered_tool_and_the_readme_documents_them(
    client: TestClient,
) -> None:
    assert sorted(client.get("/api/mcp-info").json()["tools"]) == sorted(MCP_INFO_TOOLS)
    readme = README.read_text()
    assert [name for name in MCP_INFO_TOOLS if f"`{name}`" not in readme] == []


# --- MCP -----------------------------------------------------------------------------


@pytest.fixture
def agent(session: Session) -> dict[str, Any]:
    """Project VP with a token mapped to it."""
    vp = projects.create_project(session, "VP", "VibePod")
    return {"project": vp, "token": tokens.create_token(session, "Agent", [vp.id]).token}


async def test_lists_compact_tasks_by_default_and_full_on_request(
    server_url: str,
    session: Session,
    agent: dict[str, Any],
) -> None:
    task = ideas.create_idea(
        session,
        ADMIN,
        title="Compact by default",
        project_id=agent["project"].id,
        details="x" * 2048,
        acceptance_criteria=["One", "Two"],
    )
    compact = (await call(server_url, agent["token"], "list_ideas", projectId="VP"))["items"]
    assert compact == [
        {
            "id": task.id,
            "key": "VP-1",
            "projectId": agent["project"].id,
            "taskNumber": 1,
            "title": "Compact by default",
            "status": "idea",
            "labels": [],
            "dependsOn": [],
            "blockedBy": [],
            "detailsLength": 2048,
            "acceptanceCriteriaCount": 2,
            "updatedAt": compact[0]["updatedAt"],
        }
    ]
    full = await call(server_url, agent["token"], "list_ideas", view="full")
    assert len(full["items"][0]["details"]) == 2048
    one = await call(server_url, agent["token"], "get_idea", idea="VP-1")
    assert (len(one["item"]["details"]), one["item"]["key"]) == (2048, "VP-1")


async def test_names_a_compact_tasks_dependencies_and_column_by_key(
    server_url: str,
    session: Session,
    agent: dict[str, Any],
) -> None:
    project_id = agent["project"].id
    blocker = ideas.create_idea(session, ADMIN, title="Blocker", project_id=project_id)
    waiting = ideas.create_idea(
        session, ADMIN, title="Waiting", project_id=project_id, depends_on=[blocker.id]
    )
    ideas.mark_ready(session, ADMIN, waiting.id)

    items = (await call(server_url, agent["token"], "list_ideas"))["items"]
    item = next(entry for entry in items if entry["id"] == waiting.id)
    assert (item["dependsOn"], item["blockedBy"], item["column"]) == (["VP-1"], ["VP-1"], "ready")

    card = await call(server_url, agent["token"], "get_board_card", card="VP-2")
    assert (card["item"]["ideaId"], card["item"]["key"]) == (waiting.id, "VP-2")
    assert len(card["item"]["details"]) == 0


async def test_surfaces_an_unresolvable_reference_as_a_tool_error(
    server_url: str,
    agent: dict[str, Any],
) -> None:
    with pytest.raises(ToolError, match=r"Task not found: VP-999 \(project VP, task 999\)"):
        await call(server_url, agent["token"], "get_idea", idea="VP-999")


async def test_marks_ready_with_an_inline_readiness_score(
    server_url: str,
    session: Session,
    agent: dict[str, Any],
) -> None:
    ideas.create_idea(session, ADMIN, title="Ready and rated", project_id=agent["project"].id)
    result = await call(
        server_url,
        agent["token"],
        "mark_idea_ready",
        id="VP-1",
        readiness={"score": 6, "reason": "Good enough to start"},
    )
    assert (result["item"]["readinessScore"], result["item"]["status"]) == (6, "ready")


async def test_claims_a_task_and_finds_held_and_free_work(
    server_url: str,
    agent: dict[str, Any],
) -> None:
    url, token = server_url, agent["token"]
    mine = (await call(url, token, "create_idea", title="Mine", assignee=HOLDER))["item"]
    await call(url, token, "create_idea", title="Free")

    held = (await call(url, token, "list_ideas", assignee=[HOLDER]))["items"]
    assert [(item["title"], item["assignee"]) for item in held] == [("Mine", HOLDER)]
    free = (await call(url, token, "list_ideas", unassigned=True))["items"]
    assert [item["title"] for item in free] == ["Free"]
    assert "assignee" not in free[0]

    # A guarded claim with a stale timestamp is refused.
    await call(url, token, "update_idea", id="VP-2", assignee="Codex::Session3")
    with pytest.raises(ToolError, match="Task VP-2 changed since"):
        await call(
            url,
            token,
            "update_idea",
            id="VP-2",
            assignee=HOLDER,
            expectedUpdatedAt=mine["updatedAt"],
        )

    released = await call(url, token, "update_idea", id=mine["id"], assignee="", view="full")
    assert "assignee" not in released["item"]


async def test_echoes_a_compact_task_from_a_write_and_a_ref_on_request(
    server_url: str,
    session: Session,
    agent: dict[str, Any],
) -> None:
    task = ideas.create_idea(
        session, ADMIN, title="Echo", project_id=agent["project"].id, details="x" * 4096
    )
    url, token = server_url, agent["token"]

    compact = await call(url, token, "update_idea", id="VP-1", summary="Edited")
    assert "details" not in compact["item"]
    assert compact["item"]["key"] == "VP-1"
    assert len(json.dumps(compact)) < 500

    as_ref = await call(url, token, "update_idea", id="VP-1", summary="Again", view="ref")
    assert as_ref["item"] == {
        "id": task.id,
        "key": "VP-1",
        "updatedAt": as_ref["item"]["updatedAt"],
    }

    full = await call(url, token, "update_idea", id=task.id, summary="Once more", view="full")
    assert len(full["item"]["details"]) == 4096


async def test_applies_batches_over_mcp(
    server_url: str,
    session: Session,
    agent: dict[str, Any],
) -> None:
    for title in ("One", "Two"):
        ideas.create_idea(session, ADMIN, title=title, project_id=agent["project"].id)
    url, token = server_url, agent["token"]

    refs = await call(
        url,
        token,
        "update_ideas",
        items=[
            {"id": "VP-1", "labels": ["api"], "onBoard": True},
            {"id": "VP-2", "status": "ready"},
        ],
    )
    assert [item["key"] for item in refs["items"]] == ["VP-1", "VP-2"]
    assert set(refs["items"][0]) == {"id", "key", "updatedAt"}

    moved = await call(
        url,
        token,
        "update_board_cards",
        items=[{"id": "VP-1", "column": "planned"}, {"id": "VP-2", "assignee": HOLDER}],
        view="compact",
    )
    assert [item["column"] for item in moved["items"]] == ["planned", "ready"]
    assert moved["items"][1]["assignee"] == HOLDER

    with pytest.raises(ToolError, match=r"Batch item 2 \(VP-999\) failed"):
        await call(
            url,
            token,
            "update_ideas",
            items=[{"id": "VP-1", "labels": ["rolled-back"]}, {"id": "VP-999", "labels": []}],
        )
    assert (await call(url, token, "get_idea", idea="VP-1"))["item"]["labels"] == ["api"]


async def test_lists_board_readiness_and_documents_over_mcp(
    server_url: str,
    session: Session,
    agent: dict[str, Any],
) -> None:
    project_id = agent["project"].id
    task = ideas.create_idea(session, ADMIN, title="On the board", project_id=project_id)
    ideas.mark_ready(session, ADMIN, task.id)
    readiness.set_idea_readiness(session, ADMIN, task.id, 5, "Half there")
    documents.create_document(session, ADMIN, title="Plan", project_id=project_id, content="abc")
    url, token = server_url, agent["token"]

    columns = (await call(url, token, "list_board", projectId="VP", column=["ready"]))["columns"]
    [card] = columns["ready"]
    assert (card["key"], card["readinessScore"]) == ("VP-1", 5)
    assert "details" not in card
    unheld = await call(url, token, "list_board", assignee=["Someone"])
    assert all(cards == [] for cards in unheld["columns"].values())

    latest = (await call(url, token, "list_readiness", project="VP"))["items"]
    assert [(event["ideaId"], event["score"]) for event in latest] == [(task.id, 5)]
    by_task = await call(url, token, "list_readiness", tasks=["VP-1"], latestOnly=False)
    assert len(by_task["items"]) == 1

    docs = (await call(url, token, "list_documents", kind=["execution_plan"]))["items"]
    assert (docs[0]["title"], docs[0]["contentLength"]) == ("Plan", 3)
    assert "content" not in docs[0]


async def test_pages_mcp_lists_with_a_cursor(
    server_url: str,
    session: Session,
    agent: dict[str, Any],
) -> None:
    for title in "ABC":
        ideas.create_idea(session, ADMIN, title=title, project_id=agent["project"].id)
    url, token = server_url, agent["token"]

    first = await call(url, token, "list_ideas", limit=2)
    second = await call(url, token, "list_ideas", limit=2, cursor=first["nextCursor"])
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert "nextCursor" not in second
    assert "nextCursor" not in await call(url, token, "list_ideas")


async def test_marks_read_tools_read_only(
    server_url: str,
    agent: dict[str, Any],
) -> None:
    async with Client(server_url, auth=agent["token"]) as client:
        listed = {tool.name: tool for tool in await client.list_tools()}
    for name in ("list_ideas", "get_idea", "get_board_card", "list_board", "list_readiness"):
        assert listed[name].annotations.read_only_hint is True
