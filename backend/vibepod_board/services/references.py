"""Caller-supplied references to projects, tasks and board cards.

Every identifier argument accepts, in this order: the record's id; a task key such as
`VP-236` or a project key such as `VP` (case-insensitive); a bare task number such as `236`
when exactly one project is in scope. A project also resolves by its exact title.

Task and card resolution filters on the caller's projects *before* reporting existence, so a
scoped caller cannot tell a foreign record apart from a missing one.
"""

import re
from dataclasses import dataclass

from sqlalchemy import func
from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, AdminAccess
from vibepod_board.errors import BadRequest, NotFound
from vibepod_board.graph import format_task_key
from vibepod_board.tables import BoardCardRow, IdeaRow, ProjectRow

# Task numbers start at 1, so `VP-0` and `VP-01` are typos rather than keys and fall through
# to an id lookup instead of resolving to something unintended.
_KEY_PATTERN = re.compile(r"^([A-Za-z]{1,3})-([1-9][0-9]*)$")
_NUMBER_PATTERN = re.compile(r"^([1-9][0-9]*)$")


@dataclass(frozen=True)
class TaskKey:
    project_key: str
    task_number: int


@dataclass(frozen=True)
class TaskNumber:
    task_number: int


def parse_task_reference(reference: str) -> TaskKey | TaskNumber | None:
    """`VP-236` is a key, `236` a bare number; anything else is an opaque id (None)."""
    value = reference.strip()
    if match := _KEY_PATTERN.match(value):
        return TaskKey(match.group(1).upper(), int(match.group(2)))
    if match := _NUMBER_PATTERN.match(value):
        return TaskNumber(int(match.group(1)))
    return None


def _scoped(query, access: AccessContext, project_column):
    """Restricts a query to the caller's projects; an admin sees everything."""
    if isinstance(access, AdminAccess):
        return query
    return query.where(col(project_column).in_(access.project_ids))


def resolve_project_id(session: Session, reference: str) -> str:
    """Projects resolve across the whole board; callers then check access, so a token naming
    a foreign project is refused (403) the same way whether it used the id, key or title."""
    value = reference.strip()
    rows = session.exec(
        select(ProjectRow.id, ProjectRow.key)
        .where(
            (ProjectRow.id == value)
            | (func.upper(ProjectRow.key) == value.upper())
            | (func.lower(ProjectRow.title) == value.lower())
        )
        .order_by(col(ProjectRow.key))
    ).all()
    if not rows:
        raise NotFound(f"Project not found: {value}")

    # An id or key match is exact and wins outright; titles are not unique.
    exact = [row for row in rows if row.id == value or row.key.upper() == value.upper()]
    if len(exact) == 1:
        return exact[0].id
    if len(rows) > 1:
        keys = ", ".join(row.key for row in rows)
        raise BadRequest(
            f"Ambiguous project reference: {value} matches {len(rows)} projects ({keys}); "
            "use the project key"
        )
    return rows[0].id


def resolve_idea_id(session: Session, access: AccessContext, reference: str) -> str:
    value = reference.strip()
    by_id = session.exec(
        _scoped(select(IdeaRow.id).where(IdeaRow.id == value), access, IdeaRow.project_id)
    ).first()
    if by_id:
        return by_id

    parsed = parse_task_reference(value)
    if isinstance(parsed, TaskKey):
        found = session.exec(
            _scoped(
                select(IdeaRow.id)
                .join(ProjectRow, col(ProjectRow.id) == IdeaRow.project_id)
                .where(
                    func.upper(ProjectRow.key) == parsed.project_key,
                    IdeaRow.task_number == parsed.task_number,
                ),
                access,
                IdeaRow.project_id,
            )
        ).first()
        if found:
            return found
        raise NotFound(
            f"Task not found: {value} (project {parsed.project_key}, task {parsed.task_number})"
        )

    if isinstance(parsed, TaskNumber):
        rows = session.exec(
            _scoped(
                select(IdeaRow.id, ProjectRow.key)
                .join(ProjectRow, col(ProjectRow.id) == IdeaRow.project_id)
                .where(IdeaRow.task_number == parsed.task_number),
                access,
                IdeaRow.project_id,
            ).order_by(col(ProjectRow.key))
        ).all()
        if len(rows) == 1:
            return rows[0].id
        if len(rows) > 1:
            example = format_task_key(rows[0].key, parsed.task_number)
            raise BadRequest(
                f"Ambiguous task reference: {value} matches {len(rows)} projects in scope; "
                f"use a project key such as {example}"
            )

    raise NotFound(f"Task not found: {value}")


def resolve_card_id(session: Session, access: AccessContext, reference: str) -> str:
    """A card id, or any reference to the task the card belongs to."""
    value = reference.strip()
    by_id = session.exec(
        _scoped(
            select(BoardCardRow.id).where(BoardCardRow.id == value),
            access,
            BoardCardRow.project_id,
        )
    ).first()
    if by_id:
        return by_id

    try:
        idea_id = resolve_idea_id(session, access, value)
    except (NotFound, BadRequest):
        idea_id = None
    if idea_id:
        # board_cards.idea_id carries no unique constraint, so pick deterministically.
        card_id = session.exec(
            select(BoardCardRow.id)
            .where(BoardCardRow.idea_id == idea_id)
            .order_by(col(BoardCardRow.created_at), col(BoardCardRow.id))
            .limit(1)
        ).first()
        if card_id:
            return card_id

    raise NotFound(f"Board card not found: {value}")


def require_idea_ref(session: Session, access: AccessContext, reference: str) -> IdeaRow:
    row = session.get(IdeaRow, resolve_idea_id(session, access, reference))
    assert row is not None
    return row


def require_card_ref(session: Session, access: AccessContext, reference: str) -> BoardCardRow:
    row = session.get(BoardCardRow, resolve_card_id(session, access, reference))
    assert row is not None
    return row


def task_keys(session: Session, idea_ids: list[str]) -> dict[str, str]:
    """Task id to human key, such as `VP-236`, in one indexed query."""
    ids = list(dict.fromkeys(idea_ids))
    if not ids:
        return {}
    rows = session.exec(
        select(IdeaRow.id, ProjectRow.key, IdeaRow.task_number)
        .join(ProjectRow, col(ProjectRow.id) == IdeaRow.project_id)
        .where(col(IdeaRow.id).in_(ids))
    ).all()
    return {row.id: format_task_key(row.key, row.task_number) for row in rows}
