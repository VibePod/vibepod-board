"""Project-scoped API/MCP tokens, with a permission level per area in each project. Only a
SHA-256 hash is stored; the raw token is shown once."""

import hashlib
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, update
from sqlmodel import Session, col, select

from vibepod_board import permissions as policy
from vibepod_board.errors import BadRequest, NotFound
from vibepod_board.schemas import ApiTokenSummary, CreatedApiToken, TokenProject, now
from vibepod_board.services.common import new_id, normalize_list, require_projects, transactional
from vibepod_board.tables import ApiTokenProjectRow, ApiTokenRow, ProjectRow

# A permission set (area to level), and one set per project id.
Permissions = Mapping[str, str]
ProjectPermissions = Mapping[str, Permissions]


def create_raw_token() -> str:
    return f"vbp_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class AuthenticatedToken:
    token_id: str
    project_ids: list[str]
    # The token's permission set in each of `project_ids`.
    permissions: dict[str, dict[str, str]]


def _summary(session: Session, row: ApiTokenRow) -> ApiTokenSummary:
    projects = session.exec(
        select(ProjectRow.id, ProjectRow.key, ProjectRow.title, ApiTokenProjectRow.permissions)
        .join(ApiTokenProjectRow, col(ApiTokenProjectRow.project_id) == ProjectRow.id)
        .where(ApiTokenProjectRow.token_id == row.id)
        .order_by(col(ProjectRow.key), col(ProjectRow.title))
    ).all()
    return ApiTokenSummary(
        id=row.id,
        name=row.name,
        projects=[
            TokenProject(id=id, key=key, title=title, permissions=policy.stored(granted))
            for id, key, title, granted in projects
        ],
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


def _require_token(session: Session, token_id: str, lock: bool = False) -> ApiTokenRow:
    """`lock` holds the token until the transaction ends, so that changes to its projects
    and permissions happen one after the other."""
    query = select(ApiTokenRow).where(ApiTokenRow.id == token_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.exec(query).first()
    if row is None:
        raise NotFound(f"API token not found: {token_id}")
    return row


def _project_permissions(session: Session, token_id: str) -> dict[str, dict[str, str]]:
    """The token's permission set in each project it is mapped to, by project id."""
    rows = session.exec(
        select(ApiTokenProjectRow.project_id, ApiTokenProjectRow.permissions)
        .where(ApiTokenProjectRow.token_id == token_id)
        .order_by(col(ApiTokenProjectRow.project_id))
    ).all()
    return {project_id: policy.stored(granted) for project_id, granted in rows}


def _replace_projects(session: Session, token_id: str, granted: ProjectPermissions) -> None:
    """Maps the token to exactly the projects in `granted`, each with its set."""
    session.execute(delete(ApiTokenProjectRow).where(col(ApiTokenProjectRow.token_id) == token_id))
    session.add_all(
        ApiTokenProjectRow(token_id=token_id, project_id=project_id, permissions=dict(chosen))
        for project_id, chosen in granted.items()
    )
    session.flush()


def list_tokens(session: Session) -> list[ApiTokenSummary]:
    rows = session.exec(select(ApiTokenRow).order_by(col(ApiTokenRow.created_at).desc())).all()
    return [_summary(session, row) for row in rows]


@transactional
def create_token(
    session: Session,
    name: str,
    project_ids: Sequence[str],
    permissions: Permissions | None = None,
    project_permissions: ProjectPermissions | None = None,
) -> CreatedApiToken:
    """`permissions` is one set for every project and `project_permissions` holds a set
    for single projects, which wins there. A project neither covers gets full access."""
    if not name.strip():
        raise BadRequest("Token name is required")
    if not project_ids:
        raise BadRequest("At least one project is required")
    require_projects(session, project_ids)
    granted = policy.assign(normalize_list(project_ids), permissions, project_permissions)
    token = create_raw_token()
    row = ApiTokenRow(
        id=new_id(), name=name.strip(), token_hash=hash_token(token), created_at=now()
    )
    session.add(row)
    session.flush()
    _replace_projects(session, row.id, granted)
    return CreatedApiToken(item=_summary(session, row), token=token)


@transactional
def update_token(
    session: Session,
    token_id: str,
    name: str | None = None,
    project_ids: Sequence[str] | None = None,
    permissions: Permissions | None = None,
    project_permissions: ProjectPermissions | None = None,
) -> ApiTokenSummary:
    """Changes what is given. `project_ids` replaces the token's projects; a project that
    stays keeps its permissions unless new ones are given, and a project that is added
    without any gets full access. `permissions` sets every project, `project_permissions`
    single ones."""
    # The grants are read below and replaced as a whole. Without the lock, an update that
    # commits in between would be undone by the stale grants written back here.
    row = _require_token(session, token_id, lock=True)
    next_name = name.strip() if name is not None else row.name
    if not next_name:
        raise BadRequest("Token name is required")
    row.name = next_name
    existing = _project_permissions(session, row.id)
    if project_ids is not None:
        if not project_ids:
            raise BadRequest("At least one project is required")
        require_projects(session, project_ids)
    if project_ids is not None or permissions is not None or project_permissions is not None:
        mapped = normalize_list(project_ids) if project_ids is not None else list(existing)
        granted = policy.assign(mapped, permissions, project_permissions, existing)
        _replace_projects(session, row.id, granted)
    session.flush()
    return _summary(session, row)


@transactional
def revoke_token(session: Session, token_id: str) -> ApiTokenSummary:
    row = _require_token(session, token_id)
    if row.revoked_at is None:
        row.revoked_at = now()
    session.flush()
    return _summary(session, row)


@transactional
def authenticate_token(session: Session, token: str) -> AuthenticatedToken | None:
    """Valid, unrevoked tokens are stamped with `last_used_at`."""
    token_id = session.execute(
        update(ApiTokenRow)
        .where(
            col(ApiTokenRow.token_hash) == hash_token(token), col(ApiTokenRow.revoked_at).is_(None)
        )
        .values(last_used_at=now())
        .returning(ApiTokenRow.id)
    ).scalar_one_or_none()
    if token_id is None:
        return None
    granted = _project_permissions(session, token_id)
    return AuthenticatedToken(token_id=token_id, project_ids=list(granted), permissions=granted)
