"""The needs-input and rework loop around automated runs.

An agent that cannot go on asks a question: its runner releases the task as `needs_input`,
which blocks the card with the question (see `claims.apply_release`). Answering puts the task
back in Planned, and a reviewer sends a task from Review back to Planned with feedback. The
question, the answer and the feedback stay in the task history, where the next run reads them;
a task sent back keeps its branch, so the next run continues the work there.
"""

from datetime import datetime

from sqlmodel import Session

from vibepod_board.access import AccessContext, assert_can_access_project
from vibepod_board.enums import BoardColumn, TaskEventKind
from vibepod_board.errors import BadRequest, Conflict
from vibepod_board.schemas import BoardCard, now
from vibepod_board.services.common import (
    add_activity,
    card_from_row,
    lock,
    transactional,
)
from vibepod_board.services.dependencies import decorate_card
from vibepod_board.services.history import actor_for, add_task_event
from vibepod_board.services.references import require_card_ref, task_keys
from vibepod_board.tables import BoardCardRow, IdeaRow


def _locked_card(
    session: Session, access: AccessContext, reference: str
) -> tuple[IdeaRow, BoardCardRow]:
    found = require_card_ref(session, access, reference)
    assert_can_access_project(access, found.project_id)
    if found.archived_at is not None:
        raise Conflict("Board card is archived; unarchive it first")
    if not found.idea_id:
        raise Conflict("Board card has no task")
    return lock(session, IdeaRow, found.idea_id), lock(session, BoardCardRow, found.id)


def _replan(card: BoardCardRow, idea: IdeaRow, timestamp: datetime) -> None:
    card.column_name = BoardColumn.PLANNED
    card.blocked_at = None
    card.blocked_reason = None
    card.question = None
    card.attempts = 0
    card.updated_at = timestamp
    idea.updated_at = timestamp


@transactional
def answer_question(
    session: Session, access: AccessContext, reference: str, answer: str
) -> BoardCard:
    """Answers the question a task waits on and puts it back in Planned; the next run gets
    the answer."""
    answer = (answer or "").strip()
    if not answer:
        raise BadRequest("An answer is required")
    idea, card = _locked_card(session, access, reference)
    key = task_keys(session, [idea.id]).get(idea.id, idea.id)
    if not card.question:
        raise Conflict(f"Task {key} has no question to answer")
    timestamp = now()
    actor = actor_for(session, access)
    add_task_event(session, idea.id, TaskEventKind.ANSWER, answer, actor, timestamp)
    _replan(card, idea, timestamp)
    add_activity(session, "task.answered", f"{actor} answered the question of {key}", timestamp)
    session.flush()
    return decorate_card(session, card_from_row(card))


@transactional
def request_rework(
    session: Session, access: AccessContext, reference: str, feedback: str
) -> BoardCard:
    """Sends a task from Review back to Planned with a reviewer's feedback. The card keeps
    its branch, so the next run continues the work there."""
    feedback = (feedback or "").strip()
    if not feedback:
        raise BadRequest("Feedback is required to send a task back")
    idea, card = _locked_card(session, access, reference)
    key = task_keys(session, [idea.id]).get(idea.id, idea.id)
    if card.column_name != BoardColumn.REVIEW:
        column = card.column_name.replace("_", " ")
        raise Conflict(f"Task {key} is in {column}; only tasks in review are sent back")
    timestamp = now()
    actor = actor_for(session, access)
    add_task_event(session, idea.id, TaskEventKind.FEEDBACK, feedback, actor, timestamp)
    _replan(card, idea, timestamp)
    add_activity(session, "task.rework", f"{actor} sent {key} back for rework", timestamp)
    session.flush()
    return decorate_card(session, card_from_row(card))
