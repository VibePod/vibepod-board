"""The task history: what happened to a task under automation, newest first.

Unlike the activity log, which spans all projects and keeps only its newest entries, the
history belongs to its task and is kept for as long as the task exists.
"""

from datetime import datetime, timedelta

from sqlmodel import Session, col, func, select

from vibepod_board.access import AccessContext, AdminAccess, assert_can_access_project
from vibepod_board.enums import TaskEventKind
from vibepod_board.schemas import TaskEvent
from vibepod_board.services.common import new_id
from vibepod_board.services.references import require_idea_ref
from vibepod_board.tables import ApiTokenRow, TaskEventRow


def actor_for(session: Session, access: AccessContext) -> str:
    """Names the caller in the history: the admin's username or the token's name."""
    if isinstance(access, AdminAccess):
        return access.username
    token = session.get(ApiTokenRow, access.token_id)
    return token.name if token else "API token"


def add_task_event(
    session: Session,
    idea_id: str,
    kind: TaskEventKind,
    message: str,
    actor: str | None,
    created_at: datetime,
) -> None:
    """Records an event. Events a write adds share its timestamp, so each one lands a
    millisecond after the task's latest event: the history keeps the order they happened in."""
    latest = session.exec(
        select(func.max(TaskEventRow.created_at)).where(TaskEventRow.idea_id == idea_id)
    ).one()
    if latest is not None and created_at <= latest:
        created_at = latest + timedelta(milliseconds=1)
    session.add(
        TaskEventRow(
            id=new_id(),
            idea_id=idea_id,
            kind=kind,
            actor=actor,
            message=message,
            created_at=created_at,
        )
    )


def task_event_from_row(row: TaskEventRow) -> TaskEvent:
    return TaskEvent.model_validate(row, from_attributes=True)


def list_task_history(session: Session, access: AccessContext, reference: str) -> list[TaskEvent]:
    idea = require_idea_ref(session, access, reference)
    assert_can_access_project(access, idea.project_id)
    rows = session.exec(
        select(TaskEventRow)
        .where(TaskEventRow.idea_id == idea.id)
        .order_by(col(TaskEventRow.created_at).desc(), col(TaskEventRow.id).desc())
    ).all()
    return [task_event_from_row(row) for row in rows]
