"""Shared building blocks of the service layer: row conversion, lookups, validation helpers,
the activity log and the transaction wrapper."""

import functools
import re
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy import select as sa_select
from sqlmodel import Session, col, select

from vibepod_board.errors import BadRequest, Conflict, NotFound
from vibepod_board.schemas import (
    ActivityEvent,
    BoardCard,
    Idea,
    PlanDocument,
    Project,
    ReadinessEvent,
    format_timestamp,
    now,
)
from vibepod_board.tables import (
    ActivityEventRow,
    BoardCardRow,
    DocumentRow,
    IdeaReadinessEventRow,
    IdeaRow,
    ProjectRow,
)

ACTIVITY_LIMIT = 100
PROJECT_KEY_PATTERN = re.compile(r"^[A-Z]{1,3}$")


def new_id() -> str:
    return str(uuid.uuid4())


def transactional[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Commits the session passed as first argument on success and rolls it back on error,
    so every public service call is one transaction, like the TS store's begin/commit."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        session = args[0]
        assert isinstance(session, Session)
        try:
            result = fn(*args, **kwargs)
            session.commit()
            return result
        except BaseException:
            session.rollback()
            raise

    return wrapper


# --- input normalisation -------------------------------------------------------------


def normalize_list(items: Iterable[str] | None) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items or [] if item.strip()))


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def assert_title(title: str | None, entity: str) -> None:
    if not title or not title.strip():
        raise BadRequest(f"{entity} title is required")


def normalize_project_key(key: str | None) -> str:
    if not key or not key.strip():
        raise BadRequest("Project ID is required")
    normalized = key.strip()
    if not PROJECT_KEY_PATTERN.match(normalized):
        raise BadRequest("Project ID must be 1 to 3 capital letters")
    return normalized


def normalize_readiness(score: Any, reason: str | None) -> tuple[int, str]:
    if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 10:
        raise BadRequest("Readiness score must be an integer from 1 to 10")
    reason = (reason or "").strip()
    if not reason:
        raise BadRequest("Readiness reason is required")
    return score, reason


# --- row conversion ------------------------------------------------------------------


def project_from_row(row: ProjectRow) -> Project:
    return Project.model_validate(row, from_attributes=True)


def idea_from_row(row: IdeaRow) -> Idea:
    return Idea.model_validate(row, from_attributes=True)


def card_from_row(row: BoardCardRow) -> BoardCard:
    return BoardCard(
        **row.model_dump(exclude={"column_name"}),
        column=row.column_name,
    )


def document_from_row(row: DocumentRow) -> PlanDocument:
    return PlanDocument.model_validate(row, from_attributes=True)


def readiness_from_row(row: IdeaReadinessEventRow) -> ReadinessEvent:
    return ReadinessEvent.model_validate(row, from_attributes=True)


def activity_from_row(row: ActivityEventRow) -> ActivityEvent:
    return ActivityEvent.model_validate(row, from_attributes=True)


def card_details_for_idea(idea: IdeaRow | Idea) -> str:
    return idea.details or idea.summary


# --- lookups -------------------------------------------------------------------------


def require_project(session: Session, project_id: str) -> ProjectRow:
    row = session.get(ProjectRow, project_id)
    if row is None:
        raise NotFound(f"Project not found: {project_id}")
    return row


def require_idea(session: Session, idea_id: str) -> IdeaRow:
    row = session.get(IdeaRow, idea_id)
    if row is None:
        raise NotFound(f"Task not found: {idea_id}")
    return row


def require_card(session: Session, card_id: str) -> BoardCardRow:
    row = session.get(BoardCardRow, card_id)
    if row is None:
        raise NotFound(f"Board card not found: {card_id}")
    return row


def require_document(session: Session, document_id: str) -> DocumentRow:
    row = session.get(DocumentRow, document_id)
    if row is None:
        raise NotFound(f"Document not found: {document_id}")
    return row


def require_projects(session: Session, project_ids: Iterable[str]) -> None:
    unique_ids = normalize_list(project_ids)
    found = set(session.exec(select(ProjectRow.id).where(col(ProjectRow.id).in_(unique_ids))))
    for project_id in unique_ids:
        if project_id not in found:
            raise NotFound(f"Project not found: {project_id}")


def lock[T: IdeaRow | BoardCardRow](session: Session, table: type[T], row_id: str) -> T:
    """Loads a row and holds it for the rest of the transaction. Locking before a
    concurrency guard closes the window between reading `updated_at` and writing."""
    return session.exec(
        select(table)
        .where(table.id == row_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def assert_unchanged(label: str, expected: Any, current: datetime) -> None:
    """Refuses a write when the record moved since the caller read it. The message carries
    the current value because an MCP tool error is prose and nothing else, so an agent has
    to be able to retry from the sentence."""
    if expected is None:
        return
    if isinstance(expected, str):
        try:
            expected_at = datetime.fromisoformat(expected.strip())
        except ValueError as error:
            raise BadRequest(f"expectedUpdatedAt must be an ISO timestamp: {expected}") from error
    else:
        expected_at = expected
    if expected_at.tzinfo is None:
        expected_at = expected_at.replace(tzinfo=UTC)
    if format_timestamp(expected_at) != format_timestamp(current):
        shown = expected if isinstance(expected, str) else format_timestamp(expected)
        raise Conflict(
            f"{label} changed since {shown} (current updatedAt {format_timestamp(current)})"
        )


# --- activity log --------------------------------------------------------------------


def add_activity(session: Session, type: str, message: str, created_at: Any = None) -> None:
    """Appends to the activity log and keeps only the newest entries."""
    session.add(
        ActivityEventRow(id=new_id(), type=type, message=message, created_at=created_at or now())
    )
    session.flush()
    newest = (
        sa_select(ActivityEventRow.id)
        .order_by(col(ActivityEventRow.created_at).desc())
        .limit(ACTIVITY_LIMIT)
    )
    session.execute(delete(ActivityEventRow).where(col(ActivityEventRow.id).not_in(newest)))
