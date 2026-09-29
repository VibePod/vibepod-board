"""Collapsed writes: status, board membership and readiness in one task write; card writes
that move and edit in one call; project-wide readiness reads."""

import time

import pytest
from sqlmodel import Session

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import BoardColumn, IdeaStatus
from vibepod_board.errors import BadRequest, Conflict
from vibepod_board.services import activity, board, ideas, projects, readiness

ADMIN = admin_access("admin")


@pytest.fixture
def vp(session: Session):
    return projects.create_project(session, "VP", "VibePod")


def card_for(session: Session, project, idea_id: str):
    return next(
        (card for card in board.list_cards(session, ADMIN, project.id) if card.idea_id == idea_id),
        None,
    )


def new_task(session: Session, project, title: str):
    return ideas.create_idea(session, ADMIN, title=title, project_id=project.id)


# --- task writes ---------------------------------------------------------------------


def test_creates_the_card_when_a_status_edit_reaches_ready(session: Session, vp) -> None:
    task = new_task(session, vp, "Becomes ready")
    updated = ideas.update_idea(session, ADMIN, "VP-1", status=IdeaStatus.READY, summary="Scoped")
    assert updated.status == IdeaStatus.READY
    card = card_for(session, vp, task.id)
    assert card is not None
    assert card.column == BoardColumn.READY


def test_keeps_the_card_when_the_status_moves_away_from_ready(session: Session, vp) -> None:
    task = new_task(session, vp, "Goes back to refining")
    ideas.mark_ready(session, ADMIN, task.id)
    card = card_for(session, vp, task.id)
    board.update_card(
        session, ADMIN, card.id, column=BoardColumn.IN_PROGRESS, branch_name="vp-1-work"
    )

    ideas.update_idea(session, ADMIN, task.id, status=IdeaStatus.REFINING)

    kept = card_for(session, vp, task.id)
    assert (kept.id, kept.column, kept.branch_name) == (card.id, "in_progress", "vp-1-work")


def test_removes_the_card_only_when_on_board_is_false(session: Session, vp) -> None:
    task = new_task(session, vp, "Leaves the board")
    ideas.mark_ready(session, ADMIN, task.id)
    ideas.update_idea(session, ADMIN, task.id, on_board=False)
    assert card_for(session, vp, task.id) is None


def test_refuses_to_take_an_archived_task_off_the_board(session: Session, vp) -> None:
    task = new_task(session, vp, "Archived")
    ideas.mark_ready(session, ADMIN, task.id)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.DONE)
    board.archive_card(session, ADMIN, task.id)
    with pytest.raises(Conflict, match="Task is archived"):
        ideas.update_idea(session, ADMIN, task.id, on_board=False)


def test_puts_a_task_on_the_board_when_on_board_is_true(session: Session, vp) -> None:
    task = new_task(session, vp, "Joins the board")
    updated = ideas.update_idea(session, ADMIN, task.id, on_board=True)
    assert card_for(session, vp, task.id).column == BoardColumn.READY
    assert activity.list_activity(session, ADMIN)[0].type in {"idea.ready", "board.created"}
    assert updated.id == task.id


def test_writes_content_and_readiness_in_one_call_under_one_timestamp(session: Session, vp) -> None:
    task = new_task(session, vp, "Rated while edited")
    updated = ideas.update_idea(
        session,
        ADMIN,
        task.id,
        details="Refined scope",
        status=IdeaStatus.READY,
        readiness={"score": 8, "reason": "Scope and criteria are clear"},
    )
    assert (updated.readiness_score, updated.status) == (8, IdeaStatus.READY)
    # Equal timestamps mean "evaluated as of this edit", not "already stale".
    assert updated.readiness_evaluated_at == updated.updated_at
    assert len(readiness.list_idea_readiness(session, ADMIN, task.id)) == 1
    assert card_for(session, vp, task.id).readiness_score == 8


def test_rejects_an_invalid_inline_readiness_and_writes_nothing(session: Session, vp) -> None:
    task = new_task(session, vp, "Badly rated")
    with pytest.raises(BadRequest, match="Readiness score must be an integer from 1 to 10"):
        ideas.update_idea(
            session, ADMIN, task.id, summary="Lost", readiness={"score": 11, "reason": "x"}
        )
    assert ideas.get_idea(session, ADMIN, task.id).summary == ""


def test_accepts_readiness_alongside_mark_ready(session: Session, vp) -> None:
    task = new_task(session, vp, "Ready and rated")
    ready = ideas.mark_ready(session, ADMIN, task.id, {"score": 6, "reason": "Good enough"})
    assert (ready.status, ready.readiness_score) == (IdeaStatus.READY, 6)
    assert card_for(session, vp, task.id).readiness_score == 6


def test_writes_one_task_activity_row_per_task_write(session: Session, vp) -> None:
    task = new_task(session, vp, "Counted")
    before = activity.list_activity(session, ADMIN)

    ideas.update_idea(
        session,
        ADMIN,
        task.id,
        status=IdeaStatus.READY,
        readiness={"score": 7, "reason": "Clear"},
    )

    after = activity.list_activity(session, ADMIN)

    def task_rows(rows) -> list:
        return [event for event in rows if event.type.startswith("idea.")]

    # Content, status and readiness collapse into one task event; creating the card is the
    # board's own event.
    assert len(task_rows(after)) == len(task_rows(before)) + 1
    assert {"idea.ready", "board.created"} <= {event.type for event in after}


# --- card writes ---------------------------------------------------------------------


@pytest.fixture
def card(session: Session, vp):
    task = new_task(session, vp, "Moves and edits")
    ideas.mark_ready(session, ADMIN, task.id)
    return card_for(session, vp, task.id)


def test_records_a_move_when_the_column_changes_and_an_update_otherwise(
    session: Session, card
) -> None:
    time.sleep(0.002)
    board.update_card(
        session, ADMIN, card.id, column=BoardColumn.IN_PROGRESS, branch_name="vp-1-work"
    )
    latest = activity.list_activity(session, ADMIN)[0]
    assert (latest.type, latest.message) == (
        "board.moved",
        "Moved card to in_progress: Moves and edits",
    )

    time.sleep(0.002)
    board.update_card(session, ADMIN, card.id, branch_name="vp-1-retry")
    latest = activity.list_activity(session, ADMIN)[0]
    assert (latest.type, latest.message) == ("board.updated", "Updated board card: Moves and edits")


def test_records_an_update_when_the_column_is_sent_unchanged(session: Session, card) -> None:
    time.sleep(0.002)
    board.update_card(session, ADMIN, card.id, column=BoardColumn.READY)
    assert activity.list_activity(session, ADMIN)[0].type == "board.updated"


def test_moves_a_card_by_task_key_in_one_call(session: Session, card) -> None:
    assert board.update_card(session, ADMIN, "VP-1", column=BoardColumn.REVIEW).column == "review"


def test_writes_exactly_one_activity_row_per_card_write(session: Session, card) -> None:
    before = len(activity.list_activity(session, ADMIN))
    board.update_card(session, ADMIN, card.id, column=BoardColumn.PLANNED)
    assert len(activity.list_activity(session, ADMIN)) == before + 1


# --- project readiness ---------------------------------------------------------------


@pytest.fixture
def rated(session: Session, vp):
    once = new_task(session, vp, "Rated")
    twice = new_task(session, vp, "Rated twice")
    new_task(session, vp, "Never rated")
    readiness.set_idea_readiness(session, ADMIN, once.id, 5, "Half there")
    readiness.set_idea_readiness(session, ADMIN, twice.id, 3, "Rough")
    time.sleep(0.002)
    readiness.set_idea_readiness(session, ADMIN, twice.id, 9, "Sharpened")
    return once, twice


def test_returns_the_latest_readiness_for_every_rated_task(session: Session, rated) -> None:
    once, twice = rated
    latest = {
        event.idea_id: event.score for event in readiness.list_readiness(session, ADMIN, "VP")
    }
    assert latest == {once.id: 5, twice.id: 9}


def test_returns_the_full_history_when_latest_only_is_off(session: Session, rated) -> None:
    assert len(readiness.list_readiness(session, ADMIN, "VP", latest_only=False)) == 3


def test_accepts_a_list_of_task_references(session: Session, rated) -> None:
    _, twice = rated
    [event] = readiness.list_readiness(session, ADMIN, tasks=["VP-2"])
    assert (event.idea_id, event.score) == (twice.id, 9)


def test_project_readiness_stays_inside_the_callers_projects(session: Session, vp, rated) -> None:
    other = projects.create_project(session, "OPS", "Operations")
    foreign = new_task(session, other, "Hidden")
    readiness.set_idea_readiness(session, ADMIN, foreign.id, 7, "Not yours")
    scoped = token_access("token-1", [vp.id])

    visible = readiness.list_readiness(session, scoped)
    assert len(visible) == 2
    assert all(event.idea_id != foreign.id for event in visible)
