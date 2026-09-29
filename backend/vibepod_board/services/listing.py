"""Filtered, keyset-paged lists of tasks, cards and documents.

Paging is keyset over `(updated_at desc, id desc)`. The id tiebreaker is what makes the
cursor safe: one write deliberately stamps several rows with the same timestamp, so
`updated_at` alone is not unique.
"""

import base64
import binascii
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import false, or_, tuple_
from sqlmodel import Session, col

from vibepod_board.access import AccessContext, AdminAccess, assert_can_access_project
from vibepod_board.errors import BadRequest
from vibepod_board.schemas import format_timestamp
from vibepod_board.services.references import resolve_project_id

BATCH_LIMIT = 50


@dataclass
class Page[T]:
    items: list[T]
    next_cursor: str | None = None


@dataclass(frozen=True)
class ListFilter:
    """What every list shares. `assignee` and `unassigned` widen each other: together they
    select work held by one of the named holders *or* by nobody, which is the "mine or free"
    question an agent picking up work asks. Neither given matches every row."""

    project: str | None = None
    updated_since: datetime | None = None
    assignee: Sequence[str] = field(default_factory=tuple)
    unassigned: bool = False
    limit: int | None = None
    cursor: str | None = None


def parse_timestamp(value: str | datetime | None, name: str) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise BadRequest(f"{name} must be an ISO timestamp: {value}") from error
    if parsed.tzinfo is None:
        raise BadRequest(f"{name} must carry a UTC offset: {value}")
    return parsed


def list_scope(session: Session, access: AccessContext, project: str | None) -> list[str] | None:
    """Project ids a list is restricted to: None means no predicate (an admin listing
    everything), an empty list means the caller can see nothing."""
    if project:
        project_id = resolve_project_id(session, project)
        assert_can_access_project(access, project_id)
        return [project_id]
    if isinstance(access, AdminAccess):
        return None
    return list(access.project_ids)


def scoped(query, project_column, scope: list[str] | None):
    return query if scope is None else query.where(col(project_column).in_(scope))


def assignee_clause(column, assignee: Sequence[str], unassigned: bool):
    holders = [holder for holder in assignee if holder]
    return or_(
        col(column).in_(holders) if holders else false(),
        col(column).is_(None) if unassigned else false(),
    )


def encode_cursor(updated_at: datetime, row_id: str) -> str:
    """Opaque to callers: base64url of `<updatedAt>|<id>`."""
    raw = f"{format_timestamp(updated_at)}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        updated_at, separator, row_id = base64.urlsafe_b64decode(padded).decode().partition("|")
        if not separator:
            raise ValueError(cursor)
        return datetime.fromisoformat(updated_at), row_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise BadRequest(f"Invalid cursor: {cursor}") from error


def page_query(query, table, filters: ListFilter):
    """Applies the shared filters, the keyset position and the order to a list query."""
    since = parse_timestamp(filters.updated_since, "updatedSince")
    if since is not None:
        query = query.where(col(table.updated_at) > since)
    if filters.assignee or filters.unassigned:
        query = query.where(assignee_clause(table.assignee, filters.assignee, filters.unassigned))
    if filters.cursor:
        updated_at, row_id = decode_cursor(filters.cursor)
        query = query.where(tuple_(col(table.updated_at), col(table.id)) < (updated_at, row_id))
    query = query.order_by(col(table.updated_at).desc(), col(table.id).desc())
    if filters.limit:
        query = query.limit(filters.limit)
    return query


def with_cursor[T](rows: list, items: list[T], limit: int | None) -> Page[T]:
    """A full page may have more behind it; a short one is the last."""
    if limit and rows and len(rows) == limit:
        last = rows[-1]
        return Page(items, encode_cursor(last.updated_at, last.id))
    return Page(items)
