from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, AdminAccess, assert_can_access_project
from vibepod_board.errors import Conflict
from vibepod_board.schemas import Project, now
from vibepod_board.services.common import (
    add_activity,
    assert_title,
    new_id,
    normalize_project_key,
    project_from_row,
    require_project,
    transactional,
)
from vibepod_board.services.open_reviews import validate_review_settings
from vibepod_board.services.references import resolve_project_id
from vibepod_board.tables import ProjectRow

DEFAULT_PROJECT_KEY = "GEN"
DEFAULT_PROJECT_SUMMARY = "Default project for uncategorized work."


def list_projects(session: Session, access: AccessContext | None = None) -> list[Project]:
    rows = session.exec(
        select(ProjectRow).order_by(col(ProjectRow.updated_at).desc(), col(ProjectRow.title))
    ).all()
    projects = [project_from_row(row) for row in rows]
    if access is None or isinstance(access, AdminAccess):
        return projects
    return [project for project in projects if project.id in access.project_ids]


def _assert_key_available(session: Session, key: str, current_id: str | None = None) -> None:
    query = select(ProjectRow.id).where(ProjectRow.key == key)
    if current_id is not None:
        query = query.where(ProjectRow.id != current_id)
    if session.exec(query.limit(1)).first():
        raise Conflict(f"Project ID {key} is already used")


@transactional
def create_project(
    session: Session,
    key: str,
    title: str,
    summary: str | None = None,
    required_approvals: int | None = None,
    max_review_rounds: int | None = None,
) -> Project:
    assert_title(title, "Project")
    key = normalize_project_key(key)
    validate_review_settings(required_approvals, max_review_rounds)
    timestamp = now()
    _assert_key_available(session, key)
    row = ProjectRow(
        id=new_id(),
        key=key,
        title=title.strip(),
        summary=(summary or "").strip(),
        created_at=timestamp,
        updated_at=timestamp,
    )
    _apply_review_settings(row, required_approvals, max_review_rounds)
    session.add(row)
    session.flush()
    add_activity(session, "project.created", f"Created project: {row.title}", timestamp)
    return project_from_row(row)


@transactional
def update_project(
    session: Session,
    project_id: str,
    key: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    required_approvals: int | None = None,
    max_review_rounds: int | None = None,
) -> Project:
    row = require_project(session, project_id)
    validate_review_settings(required_approvals, max_review_rounds)
    _apply_review_settings(row, required_approvals, max_review_rounds)
    if key is not None:
        key = normalize_project_key(key)
        _assert_key_available(session, key, row.id)
        row.key = key
    if title is not None:
        assert_title(title, "Project")
        row.title = title.strip()
    if summary is not None:
        row.summary = summary.strip()
    timestamp = now()
    row.updated_at = timestamp
    session.flush()
    add_activity(session, "project.updated", f"Updated project: {row.title}", timestamp)
    return project_from_row(row)


def _apply_review_settings(
    row: ProjectRow, required_approvals: int | None, max_review_rounds: int | None
) -> None:
    if required_approvals is not None:
        row.required_approvals = required_approvals
    if max_review_rounds is not None:
        row.max_review_rounds = max_review_rounds


@transactional
def update_project_settings(
    session: Session,
    access: AccessContext,
    project: str,
    required_approvals: int | None = None,
    max_review_rounds: int | None = None,
) -> Project:
    """Changes how review workers treat the project's tasks: the approvals a task needs for
    its head commit, and the rework verdicts in a row after which it is blocked. Unlike the
    rest of the project, any caller with access to the project may change them. A new
    requirement applies from the next verdict on."""
    validate_review_settings(required_approvals, max_review_rounds)
    project_id = resolve_project_id(session, project)
    assert_can_access_project(access, project_id)
    row = session.exec(
        select(ProjectRow)
        .where(ProjectRow.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    _apply_review_settings(row, required_approvals, max_review_rounds)
    timestamp = now()
    row.updated_at = timestamp
    session.flush()
    add_activity(session, "project.updated", f"Updated review settings of {row.key}", timestamp)
    return project_from_row(row)


def next_available_project_key(used: set[str], preferred: str = "PRJ") -> str:
    if preferred not in used:
        return preferred
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for length in (1, 2, 3):
        for candidate in _keys_of_length(alphabet, length):
            if candidate not in used:
                return candidate
    raise Conflict("No available project IDs")


def _keys_of_length(alphabet: str, length: int):
    if length == 1:
        yield from alphabet
        return
    for prefix in _keys_of_length(alphabet, length - 1):
        for letter in alphabet:
            yield prefix + letter


def resolve_project_id_for_create(session: Session, requested: str | None) -> str:
    """The requested project, else the oldest one, else a new "General" project."""
    if requested is not None:
        return resolve_project_id(session, requested)

    oldest = session.exec(
        select(ProjectRow).order_by(col(ProjectRow.created_at), col(ProjectRow.id)).limit(1)
    ).first()
    if oldest:
        return oldest.id

    timestamp = now()
    row = ProjectRow(
        id=new_id(),
        key=next_available_project_key(set(), DEFAULT_PROJECT_KEY),
        title="General",
        summary=DEFAULT_PROJECT_SUMMARY,
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(row)
    session.flush()
    add_activity(session, "project.created", f"Created project: {row.title}", timestamp)
    return row.id
