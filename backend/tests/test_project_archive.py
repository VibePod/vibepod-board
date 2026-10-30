"""Archiving projects: out of the active views and read-only, then restored or deleted for
good with everything in it."""

from typing import Any

import pytest
from conftest import call, login
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine, func, text
from sqlmodel import Session, select

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import BoardColumn, ClaimMode
from vibepod_board.errors import BadRequest, Conflict, Forbidden
from vibepod_board.services import (
    automation,
    board,
    claims,
    documents,
    ideas,
    project_archive,
    projects,
    tokens,
    transfer,
    workers,
)
from vibepod_board.tables import (
    ApiTokenProjectRow,
    ApiTokenRow,
    BoardCardRow,
    DocumentRow,
    IdeaRow,
    ProjectRow,
    TaskEventRow,
    WorkerRow,
)

ADMIN = admin_access("admin")
RUNNER = "claude@laptop"
REVIEWER = "codex@laptop"


@pytest.fixture
def app(session: Session):
    return projects.create_project(session, "APP", "App")


def planned(session: Session, project, title: str = "Planned"):
    task = ideas.create_idea(session, ADMIN, title=title, project_id=project.id)
    ideas.mark_ready(session, ADMIN, task.id)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED)
    return task


def archive(session: Session, project: str = "APP"):
    return project_archive.archive_project(session, ADMIN, project)


def go_silent(db: Engine, worker_id: str) -> None:
    with db.begin() as connection:
        connection.execute(
            text("update workers set last_seen_at = now() - interval '5 minutes' where id = :id"),
            {"id": worker_id},
        )


def count(session: Session, table: Any, **where: str) -> int:
    query = select(func.count()).select_from(table)
    for column, value in where.items():
        query = query.where(getattr(table, column) == value)
    return session.exec(query).one()


# --- archive ---------------------------------------------------------------------------


def test_archiving_hides_the_project_and_pauses_its_automation(session: Session, app) -> None:
    other = projects.create_project(session, "OPS", "Ops")

    archived = archive(session)

    assert archived.archived_at is not None
    assert automation.get_automation(session, ADMIN, "APP").paused
    assert [p.key for p in projects.list_projects(session, ADMIN)] == [other.key]
    assert [p.key for p in projects.list_projects(session, ADMIN, archived=True)] == ["APP"]
    assert {p.key for p in projects.list_projects(session, ADMIN, archived=None)} == {
        "APP",
        "OPS",
    }


def test_project_lists_carry_task_and_card_counts(session: Session, app) -> None:
    planned(session, app, "One")
    ideas.create_idea(session, ADMIN, title="Two", project_id=app.id)
    archive(session)

    [listed] = projects.list_projects(session, ADMIN, archived=True)

    assert (listed.task_count, listed.card_count) == (2, 1)


def test_archiving_keeps_an_earlier_pause_reason(session: Session, app) -> None:
    automation.pause_automation(session, ADMIN, "APP", "Release freeze")
    archive(session)
    assert automation.get_automation(session, ADMIN, "APP").reason == "Release freeze"


def test_archiving_twice_is_refused(session: Session, app) -> None:
    archive(session)
    with pytest.raises(Conflict, match="Project APP is already archived"):
        archive(session)


def test_archiving_is_refused_while_a_worker_is_connected(
    db: Engine, session: Session, app
) -> None:
    worker = workers.register_worker(session, ADMIN, "APP", RUNNER).item

    with pytest.raises(Conflict, match=f"workers are connected \\({RUNNER}\\)"):
        archive(session)

    go_silent(db, worker.id)
    assert archive(session).archived_at is not None


def test_archiving_is_refused_while_a_task_is_claimed(session: Session, app) -> None:
    task = planned(session, app)
    claims.claim_task(session, ADMIN, "APP", RUNNER)

    with pytest.raises(Conflict, match=r"tasks are claimed \(APP-1\)"):
        archive(session)

    claims.release_task(session, ADMIN, task.id, RUNNER)
    assert archive(session).archived_at is not None


def test_archiving_is_refused_while_a_task_is_under_review(session: Session, app) -> None:
    task = planned(session, app)
    claims.claim_task(session, ADMIN, "APP", RUNNER, task=task.id)
    claims.hand_over_task(session, ADMIN, task.id, RUNNER, branch_name="app-1", head_sha="a" * 40)
    claims.claim_task(session, ADMIN, "APP", REVIEWER, mode=ClaimMode.REVIEW)

    with pytest.raises(Conflict, match=r"tasks are under review \(APP-1\)"):
        archive(session)


# --- out of the active views and read-only ---------------------------------------------


def test_writes_to_an_archived_project_are_refused(session: Session, app) -> None:
    task = planned(session, app)
    note = documents.create_document(session, ADMIN, "Plan", project_id=app.id)
    archive(session)

    refused = "Project APP is archived"
    with pytest.raises(Conflict, match=refused):
        ideas.create_idea(session, ADMIN, title="New", project_id=app.id)
    with pytest.raises(Conflict, match=refused):
        ideas.update_idea(session, ADMIN, task.id, title="Renamed")
    with pytest.raises(Conflict, match=refused):
        ideas.delete_idea(session, ADMIN, task.id)
    with pytest.raises(Conflict, match=refused):
        board.update_card(session, ADMIN, task.id, column=BoardColumn.READY)
    with pytest.raises(Conflict, match=refused):
        documents.create_document(session, ADMIN, "Another", project_id=app.id)
    with pytest.raises(Conflict, match=refused):
        documents.update_document(session, ADMIN, note.id, content="Changed")
    with pytest.raises(Conflict, match=refused):
        claims.claim_task(session, ADMIN, "APP", RUNNER)
    with pytest.raises(Conflict, match=refused):
        workers.register_worker(session, ADMIN, "APP", RUNNER)


def test_reads_of_an_archived_project_still_work(session: Session, app) -> None:
    task = planned(session, app)
    documents.create_document(session, ADMIN, "Plan", project_id=app.id)
    archive(session)

    assert ideas.get_idea(session, ADMIN, "APP-1").id == task.id
    assert [i.id for i in ideas.list_ideas(session, ADMIN, "APP")] == [task.id]
    assert len(board.board_columns(session, ADMIN, "APP").planned) == 1
    assert [d.title for d in documents.list_documents(session, ADMIN, "APP")] == ["Plan"]


def test_unscoped_lists_leave_archived_projects_out(session: Session, app) -> None:
    planned(session, app, "Archived work")
    ops = projects.create_project(session, "OPS", "Ops")
    kept = planned(session, ops, "Active work")
    archive(session)
    token = token_access("t", [app.id, ops.id])

    for access in (ADMIN, token):
        assert [i.id for i in ideas.list_ideas(session, access)] == [kept.id]
        assert [c.idea_id for c in board.list_cards(session, access)] == [kept.id]


def test_new_work_without_a_project_skips_archived_projects(session: Session, app) -> None:
    ops = projects.create_project(session, "OPS", "Ops")
    archive(session)

    # The admin falls back to the oldest active project, a token to its first active one.
    assert ideas.create_idea(session, ADMIN, title="Admin").project_id == ops.id
    token = token_access("t", [app.id, ops.id])
    assert ideas.create_idea(session, token, title="Token").project_id == ops.id
    with pytest.raises(Forbidden, match="not mapped to any active projects"):
        ideas.create_idea(session, token_access("t", [app.id]), title="Nowhere")


def test_a_default_project_never_takes_an_archived_key(session: Session) -> None:
    general = projects.create_project(session, "GEN", "General")
    archive(session, general.id)

    created = ideas.create_idea(session, ADMIN, title="Fresh")

    assert created.project_id != general.id
    assert projects.list_projects(session, ADMIN)[0].key != "GEN"


# --- restore ---------------------------------------------------------------------------


def test_restoring_brings_the_project_back_with_automation_paused(session: Session, app) -> None:
    archive(session)

    restored = project_archive.restore_project(session, ADMIN, "APP")

    assert restored.archived_at is None
    assert [p.key for p in projects.list_projects(session, ADMIN)] == ["APP"]
    assert automation.get_automation(session, ADMIN, "APP").paused
    assert ideas.create_idea(session, ADMIN, title="Back", project_id=app.id)


def test_restoring_an_active_project_is_refused(session: Session, app) -> None:
    with pytest.raises(Conflict, match="Project APP is not archived"):
        project_archive.restore_project(session, ADMIN, "APP")


# --- delete ----------------------------------------------------------------------------


def test_an_active_project_cannot_be_deleted(session: Session, app) -> None:
    with pytest.raises(Conflict, match="archive it before deleting it"):
        project_archive.delete_project(session, ADMIN, "APP")
    assert count(session, ProjectRow) == 1


def test_deleting_needs_the_right_key_when_one_is_given(session: Session, app) -> None:
    archive(session)
    with pytest.raises(BadRequest, match="Type the project key APP"):
        project_archive.delete_project(session, ADMIN, "APP", confirm_key="OPS")
    assert count(session, ProjectRow) == 1


def test_deleting_removes_everything_in_the_project(db: Engine, session: Session, app) -> None:
    ops = projects.create_project(session, "OPS", "Ops")
    kept = planned(session, ops, "Kept")
    task = planned(session, app)
    claims.claim_task(session, ADMIN, "APP", RUNNER)
    claims.release_task(session, ADMIN, task.id, RUNNER)
    documents.create_document(session, ADMIN, "Plan", project_id=app.id)
    worker = workers.register_worker(session, ADMIN, "APP", RUNNER).item
    go_silent(db, worker.id)
    token = tokens.create_token(session, "Agent", [app.id, ops.id])
    archive(session)

    deleted = project_archive.delete_project(session, ADMIN, "APP", confirm_key="app")

    assert (deleted.id, deleted.key) == (app.id, "APP")
    for table in (IdeaRow, BoardCardRow, DocumentRow, WorkerRow, ApiTokenProjectRow):
        assert count(session, table, project_id=app.id) == 0
    assert count(session, TaskEventRow, idea_id=task.id) == 0
    # The token stays, mapped to the projects it has left; the other project is untouched.
    assert count(session, ApiTokenRow) == 1
    assert count(session, ApiTokenProjectRow, token_id=token.item.id) == 1
    assert [i.id for i in ideas.list_ideas(session, ADMIN, "OPS")] == [kept.id]


# --- REST ------------------------------------------------------------------------------


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_archive_restore_and_delete_over_rest(client: TestClient, session: Session, app) -> None:
    login(client)
    planned(session, app)

    archived = client.post(f"/api/projects/{app.id}/archive")
    assert archived.status_code == 200, archived.text
    assert archived.json()["item"]["archivedAt"]

    assert client.get("/api/projects").json()["items"] == []
    [listed] = client.get("/api/projects", params={"archived": "true"}).json()["items"]
    assert (listed["key"], listed["taskCount"], listed["cardCount"]) == ("APP", 1, 1)
    both = client.get("/api/projects", params={"includeArchived": "true"}).json()["items"]
    assert [p["key"] for p in both] == ["APP"]

    refused = client.post("/api/ideas", json={"title": "New", "projectId": app.id})
    assert refused.status_code == 409
    assert refused.json()["error"] == "Project APP is archived"

    restored = client.post(f"/api/projects/{app.id}/restore")
    assert restored.status_code == 200
    assert restored.json()["item"].get("archivedAt") is None
    assert [p["key"] for p in client.get("/api/projects").json()["items"]] == ["APP"]

    active = client.delete(f"/api/projects/{app.id}")
    assert active.status_code == 409

    client.post(f"/api/projects/{app.id}/archive")
    wrong = client.delete(f"/api/projects/{app.id}", params={"confirmKey": "NOPE"})
    assert wrong.status_code == 400
    deleted = client.delete(f"/api/projects/{app.id}", params={"confirmKey": "APP"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"id": app.id, "key": "APP", "title": "App"}
    assert client.get("/api/projects", params={"includeArchived": "true"}).json()["items"] == []


def test_archiving_over_rest_names_what_keeps_the_project_busy(
    client: TestClient, session: Session, app
) -> None:
    login(client)
    workers.register_worker(session, ADMIN, "APP", RUNNER)

    response = client.post(f"/api/projects/{app.id}/archive")

    assert response.status_code == 409
    assert RUNNER in response.json()["error"]


def test_archive_restore_and_delete_need_the_admin(
    client: TestClient, session: Session, app
) -> None:
    token = tokens.create_token(session, "Agent", [app.id]).token
    for method, path in (
        ("post", f"/api/projects/{app.id}/archive"),
        ("post", f"/api/projects/{app.id}/restore"),
        ("delete", f"/api/projects/{app.id}"),
    ):
        response = getattr(client, method)(path, headers=bearer(token))
        assert response.status_code == 403, (path, response.text)


# --- MCP -------------------------------------------------------------------------------


async def test_archive_restore_and_delete_over_mcp(server_url: str, session: Session, app) -> None:
    other = projects.create_project(session, "OPS", "Ops")
    token = tokens.create_token(session, "Agent", [app.id]).token
    url = server_url

    archived = await call(url, token, "archive_project", projectId="APP")
    assert archived["item"]["archivedAt"]
    assert (await call(url, token, "list_projects"))["items"] == []
    [listed] = (await call(url, token, "list_projects", archived=True))["items"]
    assert listed["key"] == "APP"
    both = await call(url, token, "list_projects", includeArchived=True)
    assert [p["key"] for p in both["items"]] == ["APP"]

    with pytest.raises(ToolError, match="Project APP is archived"):
        await call(url, token, "create_idea", title="New", projectId="APP")

    restored = await call(url, token, "restore_project", projectId="APP")
    assert restored["item"].get("archivedAt") is None
    with pytest.raises(ToolError, match="archive it before deleting it"):
        await call(url, token, "delete_project", projectId="APP", confirmKey="APP")

    await call(url, token, "archive_project", projectId="APP")
    with pytest.raises(ToolError, match="Type the project key APP"):
        await call(url, token, "delete_project", projectId="APP", confirmKey="OPS")
    deleted = await call(url, token, "delete_project", projectId="APP", confirmKey="APP")
    assert deleted["item"]["key"] == "APP"
    assert [p.key for p in projects.list_projects(session, ADMIN, archived=None)] == [other.key]


async def test_mcp_tokens_only_archive_their_own_projects(
    server_url: str, session: Session, app
) -> None:
    projects.create_project(session, "OPS", "Ops")
    token = tokens.create_token(session, "Agent", [app.id]).token

    with pytest.raises(ToolError, match="not allowed"):
        await call(server_url, token, "archive_project", projectId="OPS")


def test_an_archived_project_keeps_its_details_and_its_data(session: Session, app) -> None:
    planned(session, app)
    bundle = transfer.export_project(session, app.id)
    archive(session)

    with pytest.raises(Conflict, match="Project APP is archived"):
        projects.update_project(session, app.id, title="Renamed")
    with pytest.raises(Conflict, match="Project APP is archived"):
        projects.update_project_settings(session, ADMIN, "APP", required_approvals=2)
    with pytest.raises(Conflict, match="restore it before replacing it"):
        transfer.import_project(session, bundle, replace_existing=True)
    # Exporting an archived project still works.
    assert transfer.export_project(session, app.id).project.key == "APP"
