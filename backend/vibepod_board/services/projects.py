from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, AdminAccess
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
def create_project(session: Session, key: str, title: str, summary: str | None = None) -> Project:
    assert_title(title, "Project")
    key = normalize_project_key(key)
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
) -> Project:
    row = require_project(session, project_id)
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
        return require_project(session, requested).id

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
