"""Archiving done board cards: hidden from the board, listed per project, restorable."""

import pytest
from conftest import login
from fastapi.testclient import TestClient
from github_fake import FakeGitHub
from sqlmodel import Session

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import BoardColumn
from vibepod_board.errors import Conflict, Forbidden, NotFound
from vibepod_board.github import GitHubClient
from vibepod_board.services import board, github_sync, ideas, projects, readiness, transfer
from vibepod_board.tables import BoardCardRow

ADMIN = admin_access("admin")
REPO = "vibepod/board"


@pytest.fixture
def project(session: Session):
    return projects.create_project(session, "APP", "App")


def done_card(session: Session, project, title: str = "Shipped", **fields):
    """A task whose card sits in the done column."""
    task = ideas.create_idea(session, ADMIN, title=title, project_id=project.id, **fields)
    ideas.mark_ready(session, ADMIN, task.id)
    card = next(c for c in board.list_cards(session, ADMIN, project.id) if c.idea_id == task.id)
    board.move_card(session, ADMIN, card.id, BoardColumn.DONE)
    return task, card


# --- service -------------------------------------------------------------------------


def test_archives_a_done_card_and_hides_it_from_the_board(session: Session, project) -> None:
    task, card = done_card(session, project)

    archived = board.archive_card(session, ADMIN, card.id)

    assert archived.archived_at is not None
    assert archived.column == BoardColumn.DONE
    assert board.board_columns(session, ADMIN, project.id).done == []
    assert [c.id for c in board.list_archived_cards(session, ADMIN, project.id)] == [card.id]
    # The task itself is untouched and still counts as finished for its dependents.
    [kept] = ideas.list_ideas(session, ADMIN, project.id)
    assert (kept.id, kept.status) == (task.id, "ready")


@pytest.mark.parametrize("column", [BoardColumn.READY, BoardColumn.PR_READY])
def test_only_done_cards_can_be_archived(session: Session, project, column: BoardColumn) -> None:
    task = ideas.create_idea(session, ADMIN, title="Busy", project_id=project.id)
    ideas.mark_ready(session, ADMIN, task.id)
    card = board.board_columns(session, ADMIN, project.id).ready[0]

    board.move_card(session, ADMIN, card.id, column)
    with pytest.raises(Conflict, match="Only cards in the done column can be archived"):
        board.archive_card(session, ADMIN, card.id)

    assert board.list_archived_cards(session, ADMIN, project.id) == []


def test_archiving_twice_and_unarchiving_an_active_card_are_rejected(
    session: Session, project
) -> None:
    _, card = done_card(session, project)
    with pytest.raises(Conflict, match="Board card is not archived"):
        board.unarchive_card(session, ADMIN, card.id)
    board.archive_card(session, ADMIN, card.id)
    with pytest.raises(Conflict, match="Board card is already archived"):
        board.archive_card(session, ADMIN, card.id)


def test_unarchive_restores_the_card_to_done(session: Session, project) -> None:
    _, card = done_card(session, project)
    board.archive_card(session, ADMIN, card.id)

    restored = board.unarchive_card(session, ADMIN, card.id)

    assert restored.archived_at is None
    assert restored.column == BoardColumn.DONE
    assert [c.id for c in board.board_columns(session, ADMIN, project.id).done] == [card.id]
    assert board.list_archived_cards(session, ADMIN, project.id) == []


def test_archives_every_done_card_of_one_project(session: Session, project) -> None:
    other = projects.create_project(session, "OTH", "Other")
    _, first = done_card(session, project, "First")
    _, second = done_card(session, project, "Second")
    _, elsewhere = done_card(session, other, "Elsewhere")
    active = ideas.create_idea(session, ADMIN, title="Active", project_id=project.id)
    ideas.mark_ready(session, ADMIN, active.id)

    archived = board.archive_done_cards(session, ADMIN, project.id)

    assert {c.id for c in archived} == {first.id, second.id}
    columns = board.board_columns(session, ADMIN, project.id)
    assert columns.done == []
    assert [c.title for c in columns.ready] == ["Active"]
    assert [c.id for c in board.board_columns(session, ADMIN, other.id).done] == [elsewhere.id]
    assert board.archive_done_cards(session, ADMIN, project.id) == []


def test_archived_cards_cannot_be_moved_or_edited(session: Session, project) -> None:
    _, card = done_card(session, project)
    board.archive_card(session, ADMIN, card.id)

    with pytest.raises(Conflict, match="Board card is archived"):
        board.move_card(session, ADMIN, card.id, BoardColumn.READY)
    with pytest.raises(Conflict, match="Board card is archived"):
        board.update_card(session, ADMIN, card.id, column=BoardColumn.REVIEW)
    with pytest.raises(Conflict, match="Board card is archived"):
        board.update_card(session, ADMIN, card.id, branch_name="late-change")

    assert board.board_columns(session, ADMIN, project.id).done == []


def test_task_updates_refresh_but_never_resurrect_an_archived_card(
    session: Session, project
) -> None:
    task, card = done_card(session, project)
    board.archive_card(session, ADMIN, card.id)

    ideas.update_idea(session, ADMIN, task.id, title="Renamed", labels=["later"])
    ideas.mark_ready(session, ADMIN, task.id)
    readiness.set_idea_readiness(session, ADMIN, task.id, 9, "Still clear")

    assert board.board_columns(session, ADMIN, project.id).done == []
    [archived] = board.list_archived_cards(session, ADMIN, project.id)
    assert (archived.id, archived.title, archived.labels) == (card.id, "Renamed", ["later"])
    assert archived.readiness_score == 9


def test_scoring_an_archived_card_goes_to_its_task(session: Session, project) -> None:
    task, card = done_card(session, project)
    board.archive_card(session, ADMIN, card.id)

    scored = readiness.set_card_readiness(session, ADMIN, card.id, 8, "Clear")

    assert scored.readiness_score == 8
    assert readiness.list_idea_readiness(session, ADMIN, task.id)[0].score == 8
    assert board.board_columns(session, ADMIN, project.id).done == []


def test_an_archived_card_without_a_task_cannot_be_scored(session: Session, project) -> None:
    _, card = done_card(session, project)
    board.archive_card(session, ADMIN, card.id)
    row = session.get(BoardCardRow, card.id)
    assert row is not None
    row.idea_id = None
    session.commit()

    with pytest.raises(Conflict, match="Board card is archived"):
        readiness.set_card_readiness(session, ADMIN, card.id, 8, "Clear")
    [archived] = board.list_archived_cards(session, ADMIN, project.id)
    assert archived.readiness_score is None


def test_an_archived_task_cannot_be_taken_off_the_board(session: Session, project) -> None:
    task, card = done_card(session, project)
    board.archive_card(session, ADMIN, card.id)

    with pytest.raises(Conflict, match="Task is archived"):
        ideas.set_board_availability(session, ADMIN, task.id, False)

    assert [c.id for c in board.list_archived_cards(session, ADMIN, project.id)] == [card.id]


def test_github_pull_keeps_an_archived_card_archived(session: Session, project) -> None:
    fake = FakeGitHub()
    fake.add_issue(REPO, 7, "Remote title", labels=("bug",))
    task, card = done_card(session, project, "Local")
    ideas.update_idea(
        session, ADMIN, task.id, github_issue_url=f"https://github.com/{REPO}/issues/7"
    )
    board.archive_card(session, ADMIN, card.id)

    github_sync.pull(session, ADMIN, task.id, GitHubClient("token", fake.transport))

    assert board.board_columns(session, ADMIN, project.id).done == []
    [archived] = board.list_archived_cards(session, ADMIN, project.id)
    assert (archived.title, archived.github_issue_number) == ("Remote title", 7)


def test_archived_blockers_still_count_as_done(session: Session, project) -> None:
    blocker, card = done_card(session, project, "Blocker")
    dependent = ideas.create_idea(
        session, ADMIN, title="Dependent", project_id=project.id, depends_on=[blocker.id]
    )
    board.archive_card(session, ADMIN, card.id)

    [decorated] = [i for i in ideas.list_ideas(session, ADMIN, project.id) if i.id == dependent.id]
    assert decorated.blocked_by == []
    order = ideas.work_order(session, ADMIN, project.id)
    assert {item.title: item.is_complete for item in order.items} == {
        "Blocker": True,
        "Dependent": False,
    }


def test_archive_stays_inside_the_token_scope(session: Session, project) -> None:
    other = projects.create_project(session, "OTH", "Other")
    _, theirs = done_card(session, other, "Theirs")
    _, mine = done_card(session, project, "Mine")
    board.archive_card(session, ADMIN, theirs.id)
    board.archive_card(session, ADMIN, mine.id)
    scoped = token_access("token-1", [project.id])

    with pytest.raises(NotFound, match=f"Board card not found: {theirs.id}"):
        board.unarchive_card(session, scoped, theirs.id)
    with pytest.raises(Forbidden, match="Token is not allowed to access project"):
        board.list_archived_cards(session, scoped, other.id)
    with pytest.raises(Forbidden, match="Token is not allowed to access project"):
        board.archive_done_cards(session, scoped, other.id)
    assert [c.title for c in board.list_archived_cards(session, scoped)] == ["Mine"]


def test_archived_cards_survive_export_and_import(session: Session, project) -> None:
    _, card = done_card(session, project)
    archived = board.archive_card(session, ADMIN, card.id)

    bundle = transfer.export_project(session, project.id)
    assert bundle.bundle_version == 4
    assert bundle.board_cards[0].archived_at == archived.archived_at

    transfer.import_project(session, bundle, replace_existing=True)

    assert board.board_columns(session, ADMIN, project.id).done == []
    [restored] = board.list_archived_cards(session, ADMIN, project.id)
    assert (restored.id, restored.archived_at) == (card.id, archived.archived_at)


# --- REST ----------------------------------------------------------------------------


def test_rest_archive_flow(client: TestClient) -> None:
    agent = login(client)
    project = agent.post("/api/projects", json={"key": "APP", "title": "App"}).json()["item"]
    task = agent.post("/api/ideas", json={"title": "Ship", "projectId": project["id"]}).json()[
        "item"
    ]
    agent.post(f"/api/ideas/{task['id']}/ready")
    card_id = agent.get("/api/board").json()["columns"]["ready"][0]["id"]

    rejected = agent.post(f"/api/board/{card_id}/archive")
    assert rejected.status_code == 409
    assert rejected.json()["error"] == "Only cards in the done column can be archived"

    agent.patch(f"/api/board/{card_id}", json={"column": "done"})
    archived = agent.post(f"/api/board/{card_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["item"]["archivedAt"]
    assert agent.get("/api/board").json()["columns"]["done"] == []
    listed = agent.get("/api/board/archived", params={"projectId": project["id"]}).json()
    assert [item["id"] for item in listed["items"]] == [card_id]

    assert agent.patch(f"/api/board/{card_id}", json={"column": "ready"}).status_code == 409

    restored = agent.post(f"/api/board/{card_id}/unarchive")
    assert restored.status_code == 200
    assert "archivedAt" not in restored.json()["item"]
    assert [c["id"] for c in agent.get("/api/board").json()["columns"]["done"]] == [card_id]

    bulk = agent.post("/api/board/archive-done", json={"projectId": project["id"]})
    assert bulk.status_code == 200
    assert [item["id"] for item in bulk.json()["items"]] == [card_id]
    assert agent.get("/api/board/archived").json()["items"][0]["id"] == card_id

    anonymous = TestClient(client.app)
    assert anonymous.get("/api/board/archived").status_code == 401
    assert anonymous.post(f"/api/board/{card_id}/unarchive").status_code == 401
