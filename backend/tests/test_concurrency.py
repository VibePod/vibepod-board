"""Optimistic concurrency: task and card writes refuse a stale expectedUpdatedAt."""

import time

import pytest
from conftest import login
from fastapi.testclient import TestClient
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.enums import BoardColumn
from vibepod_board.errors import BadRequest, Conflict
from vibepod_board.schemas import format_timestamp
from vibepod_board.services import board, dependencies, ideas, projects, readiness

ADMIN = admin_access("admin")


@pytest.fixture
def vp(session: Session):
    return projects.create_project(session, "VP", "VibePod")


@pytest.fixture
def task(session: Session, vp):
    created = ideas.create_idea(session, ADMIN, title="Contended", project_id=vp.id)
    # Timestamps have millisecond resolution; keep the next write in a later millisecond.
    time.sleep(0.002)
    return created


def test_accepts_a_current_timestamp_and_rejects_a_stale_one(session: Session, task) -> None:
    first = ideas.update_idea(
        session, ADMIN, task.id, summary="First writer", expected_updated_at=task.updated_at
    )
    stamp = format_timestamp(task.updated_at)
    with pytest.raises(Conflict, match=f"Task VP-1 changed since {stamp}"):
        ideas.update_idea(
            session, ADMIN, task.id, summary="Second writer", expected_updated_at=stamp
        )
    fresh = ideas.get_idea(session, ADMIN, task.id)
    assert fresh.summary == "First writer"
    assert fresh.updated_at == first.updated_at


def test_reports_the_current_timestamp_in_the_conflict_message(session: Session, task) -> None:
    moved = ideas.update_idea(session, ADMIN, task.id, summary="Moved on")
    with pytest.raises(Conflict, match=f"current updatedAt {format_timestamp(moved.updated_at)}"):
        ideas.update_idea(
            session, ADMIN, task.id, summary="Stale", expected_updated_at=task.updated_at
        )


def test_accepts_the_timestamp_in_another_offset(session: Session, task) -> None:
    shifted = task.updated_at.astimezone().isoformat()
    ideas.update_idea(session, ADMIN, task.id, summary="Same instant", expected_updated_at=shifted)


def test_refuses_a_malformed_timestamp(session: Session, task) -> None:
    with pytest.raises(BadRequest, match="expectedUpdatedAt must be an ISO timestamp"):
        ideas.update_idea(session, ADMIN, task.id, summary="x", expected_updated_at="soon")


def test_guards_card_writes_too(session: Session, vp, task) -> None:
    ideas.mark_ready(session, ADMIN, task.id)
    card = board.get_card(session, ADMIN, task.id)
    time.sleep(0.002)
    board.update_card(session, ADMIN, card.id, column=BoardColumn.PLANNED)

    with pytest.raises(Conflict, match="Board card changed since"):
        board.update_card(
            session,
            ADMIN,
            card.id,
            column=BoardColumn.REVIEW,
            expected_updated_at=card.updated_at,
        )
    assert len(board.board_columns(session, ADMIN, vp.id).planned) == 1


def test_bumps_updated_at_on_a_dependency_write(session: Session, vp, task) -> None:
    blocker = ideas.create_idea(session, ADMIN, title="Blocker", project_id=vp.id)
    time.sleep(0.002)
    linked = dependencies.set_dependencies(session, ADMIN, task.id, [blocker.id])
    assert linked.updated_at != task.updated_at
    with pytest.raises(Conflict, match="changed since"):
        ideas.update_idea(
            session, ADMIN, task.id, summary="Stale", expected_updated_at=task.updated_at
        )


def test_leaves_updated_at_alone_on_a_readiness_write(session: Session, task) -> None:
    # The staleness badge compares content time against evaluation time, so a readiness
    # write must not age the record.
    scored = readiness.set_idea_readiness(session, ADMIN, task.id, 7, "Clear enough")
    assert scored.updated_at == task.updated_at
    ideas.update_idea(
        session, ADMIN, task.id, summary="Still writable", expected_updated_at=task.updated_at
    )


def test_applies_a_write_with_no_guard_exactly_as_before(session: Session, task) -> None:
    ideas.update_idea(session, ADMIN, task.id, summary="One")
    assert ideas.update_idea(session, ADMIN, task.id, summary="Two").summary == "Two"


def test_rest_answers_409_when_a_guarded_write_is_stale(client: TestClient) -> None:
    login(client)
    client.post("/api/projects", json={"key": "VP", "title": "VibePod"})
    task = client.post("/api/ideas", json={"projectId": "VP", "title": "Contended"}).json()["item"]
    time.sleep(0.002)
    client.patch(f"/api/ideas/{task['id']}", json={"summary": "Moved on"})

    conflict = client.patch(
        f"/api/ideas/{task['id']}",
        json={"summary": "Stale", "expectedUpdatedAt": task["updatedAt"]},
    )
    assert conflict.status_code == 409
    assert "changed since" in conflict.json()["error"]

    fresh = client.get(f"/api/ideas/{task['id']}").json()["item"]
    accepted = client.patch(
        f"/api/ideas/{task['id']}",
        json={"summary": "Fresh", "expectedUpdatedAt": fresh["updatedAt"]},
    )
    assert accepted.status_code == 200
