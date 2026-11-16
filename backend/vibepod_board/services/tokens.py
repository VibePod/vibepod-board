"""Project-scoped API/MCP tokens, with a permission level per area in each project. Only a
SHA-256 hash is stored; the raw token is shown once.

A token belongs to the admin or to a user. A user's token reaches only projects that are
assigned to that user, and only while the user may sign in. The functions that take
`owner_user_id` act for that user and see nothing but that user's tokens; without it they
act for the admin, who sees every token.
"""

import hashlib
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, or_, update
from sqlmodel import Session, col, select

from vibepod_board import permissions as policy
from vibepod_board.errors import BadRequest, Forbidden, NotFound
from vibepod_board.schemas import ApiTokenSummary, CreatedApiToken, TokenProject, now
from vibepod_board.services.common import new_id, normalize_list, require_projects, transactional
from vibepod_board.tables import (
    ApiTokenProjectRow,
    ApiTokenRow,
    ProjectRow,
    UserProjectRow,
    UserRow,
)

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
    owner = session.get(UserRow, row.owner_user_id) if row.owner_user_id else None
    return ApiTokenSummary(
        id=row.id,
        name=row.name,
        owner=owner.username if owner else None,
        projects=[
            TokenProject(id=id, key=key, title=title, permissions=policy.stored(granted))
            for id, key, title, granted in projects
        ],
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


def _require_token(
    session: Session, token_id: str, lock: bool = False, owner_user_id: str | None = None
) -> ApiTokenRow:
    """`lock` holds the token until the transaction ends, so that changes to its projects
    and permissions happen one after the other. For a user, a token of somebody else does
    not exist."""
    query = select(ApiTokenRow).where(ApiTokenRow.id == token_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.exec(query).first()
    if row is None or (owner_user_id is not None and row.owner_user_id != owner_user_id):
        raise NotFound(f"API token not found: {token_id}")
    return row


def _assigned_projects(session: Session, user_id: str) -> set[str]:
    return set(
        session.exec(select(UserProjectRow.project_id).where(UserProjectRow.user_id == user_id))
    )


def assert_owner_reaches(
    session: Session, owner_user_id: str | None, project_ids: Sequence[str], by_owner: bool
) -> None:
    """A token that belongs to a user may name only projects assigned to that user.
    `by_owner` words the refusal for the user; otherwise it names them for the admin."""
    if owner_user_id is None:
        return
    assigned = _assigned_projects(session, owner_user_id)
    for project_id in normalize_list(project_ids):
        if project_id in assigned:
            continue
        if by_owner:
            raise Forbidden(f"You have no access to project: {project_id}")
        owner = session.get(UserRow, owner_user_id)
        name = owner.username if owner else "The owner"
        raise Forbidden(f"{name} has no access to project: {project_id}")


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


def list_tokens(session: Session, owner_user_id: str | None = None) -> list[ApiTokenSummary]:
    query = select(ApiTokenRow).order_by(col(ApiTokenRow.created_at).desc())
    if owner_user_id is not None:
        query = query.where(ApiTokenRow.owner_user_id == owner_user_id)
    return [_summary(session, row) for row in session.exec(query).all()]


@transactional
def create_token(
    session: Session,
    name: str,
    project_ids: Sequence[str],
    permissions: Permissions | None = None,
    project_permissions: ProjectPermissions | None = None,
    owner_user_id: str | None = None,
) -> CreatedApiToken:
    """`permissions` is one set for every project and `project_permissions` holds a set
    for single projects, which wins there. A project neither covers gets full access.
    With `owner_user_id` the token belongs to that user."""
    if not name.strip():
        raise BadRequest("Token name is required")
    if not project_ids:
        raise BadRequest("At least one project is required")
    require_projects(session, project_ids)
    assert_owner_reaches(session, owner_user_id, project_ids, by_owner=True)
    granted = policy.assign(normalize_list(project_ids), permissions, project_permissions)
    token = create_raw_token()
    row = ApiTokenRow(
        id=new_id(),
        name=name.strip(),
        token_hash=hash_token(token),
        owner_user_id=owner_user_id,
        created_at=now(),
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
    owner_user_id: str | None = None,
) -> ApiTokenSummary:
    """Changes what is given. `project_ids` replaces the token's projects; a project that
    stays keeps its permissions unless new ones are given, and a project that is added
    without any gets full access. `permissions` sets every project, `project_permissions`
    single ones."""
    # The grants are read below and replaced as a whole. Without the lock, an update that
    # commits in between would be undone by the stale grants written back here.
    row = _require_token(session, token_id, lock=True, owner_user_id=owner_user_id)
    next_name = name.strip() if name is not None else row.name
    if not next_name:
        raise BadRequest("Token name is required")
    row.name = next_name
    existing = _project_permissions(session, row.id)
    if project_ids is not None:
        if not project_ids:
            raise BadRequest("At least one project is required")
        require_projects(session, project_ids)
        # Also when the admin edits a user's token: it stays within the user's projects.
        assert_owner_reaches(
            session, row.owner_user_id, project_ids, by_owner=owner_user_id is not None
        )
    if project_ids is not None or permissions is not None or project_permissions is not None:
        mapped = normalize_list(project_ids) if project_ids is not None else list(existing)
        granted = policy.assign(mapped, permissions, project_permissions, existing)
        _replace_projects(session, row.id, granted)
    session.flush()
    return _summary(session, row)


@transactional
def revoke_token(
    session: Session, token_id: str, owner_user_id: str | None = None
) -> ApiTokenSummary:
    row = _require_token(session, token_id, owner_user_id=owner_user_id)
    if row.revoked_at is None:
        row.revoked_at = now()
    session.flush()
    return _summary(session, row)


@transactional
def authenticate_token(session: Session, token: str) -> AuthenticatedToken | None:
    """Valid, unrevoked tokens are stamped with `last_used_at`. A user's token counts only
    while the user is active, and reaches only the projects the user still has: this is
    where a deactivation and a removed project take effect, whatever the token's own
    links say."""
    active_users = select(UserRow.id).where(col(UserRow.active).is_(True))
    found = session.execute(
        update(ApiTokenRow)
        .where(
            col(ApiTokenRow.token_hash) == hash_token(token),
            col(ApiTokenRow.revoked_at).is_(None),
            or_(
                col(ApiTokenRow.owner_user_id).is_(None),
                col(ApiTokenRow.owner_user_id).in_(active_users),
            ),
        )
        .values(last_used_at=now())
        .returning(ApiTokenRow.id, ApiTokenRow.owner_user_id)
    ).first()
    if found is None:
        return None
    token_id, owner_user_id = found
    granted = _project_permissions(session, token_id)
    if owner_user_id is not None:
        assigned = _assigned_projects(session, owner_user_id)
        granted = {
            project_id: chosen for project_id, chosen in granted.items() if project_id in assigned
        }
    return AuthenticatedToken(token_id=token_id, project_ids=list(granted), permissions=granted)
