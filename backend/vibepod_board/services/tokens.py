"""Project-scoped API/MCP tokens. Only a SHA-256 hash is stored; the raw token is shown once."""

import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, update
from sqlmodel import Session, col, select

from vibepod_board.errors import BadRequest, NotFound
from vibepod_board.schemas import ApiTokenSummary, CreatedApiToken, ProjectRef, now
from vibepod_board.services.common import new_id, normalize_list, require_projects, transactional
from vibepod_board.tables import ApiTokenProjectRow, ApiTokenRow, ProjectRow


def create_raw_token() -> str:
    return f"vbp_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class AuthenticatedToken:
    token_id: str
    project_ids: list[str]


def _summary(session: Session, row: ApiTokenRow) -> ApiTokenSummary:
    projects = session.exec(
        select(ProjectRow.id, ProjectRow.key, ProjectRow.title)
        .join(ApiTokenProjectRow, col(ApiTokenProjectRow.project_id) == ProjectRow.id)
        .where(ApiTokenProjectRow.token_id == row.id)
        .order_by(col(ProjectRow.key), col(ProjectRow.title))
    ).all()
    return ApiTokenSummary(
        id=row.id,
        name=row.name,
        projects=[ProjectRef(id=id, key=key, title=title) for id, key, title in projects],
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


def _require_token(session: Session, token_id: str) -> ApiTokenRow:
    row = session.get(ApiTokenRow, token_id)
    if row is None:
        raise NotFound(f"API token not found: {token_id}")
    return row


def _replace_projects(session: Session, token_id: str, project_ids: Sequence[str]) -> None:
    session.execute(delete(ApiTokenProjectRow).where(col(ApiTokenProjectRow.token_id) == token_id))
    session.add_all(
        ApiTokenProjectRow(token_id=token_id, project_id=project_id)
        for project_id in normalize_list(project_ids)
    )
    session.flush()


def list_tokens(session: Session) -> list[ApiTokenSummary]:
    rows = session.exec(select(ApiTokenRow).order_by(col(ApiTokenRow.created_at).desc())).all()
    return [_summary(session, row) for row in rows]


@transactional
def create_token(session: Session, name: str, project_ids: Sequence[str]) -> CreatedApiToken:
    if not name.strip():
        raise BadRequest("Token name is required")
    if not project_ids:
        raise BadRequest("At least one project is required")
    require_projects(session, project_ids)
    token = create_raw_token()
    row = ApiTokenRow(
        id=new_id(), name=name.strip(), token_hash=hash_token(token), created_at=now()
    )
    session.add(row)
    session.flush()
    _replace_projects(session, row.id, project_ids)
    return CreatedApiToken(item=_summary(session, row), token=token)


@transactional
def update_token(
    session: Session,
    token_id: str,
    name: str | None = None,
    project_ids: Sequence[str] | None = None,
) -> ApiTokenSummary:
    row = _require_token(session, token_id)
    next_name = name.strip() if name is not None else row.name
    if not next_name:
        raise BadRequest("Token name is required")
    row.name = next_name
    if project_ids is not None:
        if not project_ids:
            raise BadRequest("At least one project is required")
        require_projects(session, project_ids)
        _replace_projects(session, row.id, project_ids)
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
    project_ids = session.exec(
        select(ApiTokenProjectRow.project_id)
        .where(ApiTokenProjectRow.token_id == token_id)
        .order_by(col(ApiTokenProjectRow.project_id))
    ).all()
    return AuthenticatedToken(token_id=token_id, project_ids=list(project_ids))
