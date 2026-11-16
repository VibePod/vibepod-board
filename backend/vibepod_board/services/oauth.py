"""OAuth sign-in storage: registered clients, sign-in requests and their one-time codes.

A request lives from `/authorize` until its code is exchanged for a token, it is denied, or
it expires. Only a SHA-256 hash of the code is stored. The token a sign-in issues is an
ordinary API token, scoped to the projects and permissions that the admin or a user chose,
and it belongs to whoever approved the sign-in.
"""

import hashlib
import secrets
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import delete
from sqlmodel import Session, col, select

from vibepod_board import permissions as policy
from vibepod_board.errors import BadRequest, NotFound
from vibepod_board.schemas import CreatedApiToken, OAuthRequestInfo, now
from vibepod_board.services import tokens
from vibepod_board.services.common import normalize_list, require_projects, transactional
from vibepod_board.tables import OAuthClientRow, OAuthRequestRow

# How long the admin has to approve, and how long the client has to exchange the code.
REQUEST_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=5)

DEFAULT_CLIENT_NAME = "MCP client"
CLIENT_NAME_LIMIT = 80


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _redirect(redirect_uri: str, **params: str | None) -> str:
    """`redirect_uri` with `params` added to its query; parameters without a value are left out."""
    parsed = urlparse(redirect_uri)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query += [(key, value) for key, value in params.items() if value is not None]
    return urlunparse(parsed._replace(query=urlencode(query)))


def _client_name(session: Session, client_id: str) -> str:
    client = session.get(OAuthClientRow, client_id)
    name = str((client.info if client else {}).get("client_name") or "").strip()
    return name[:CLIENT_NAME_LIMIT] or DEFAULT_CLIENT_NAME


def _waiting(session: Session, request_id: str) -> OAuthRequestRow:
    """The request, while it still waits for the admin's decision."""
    row = session.get(OAuthRequestRow, request_id)
    if row is None or row.code_hash is not None or row.expires_at <= now():
        raise NotFound("Sign-in request not found or expired")
    return row


@transactional
def save_client(session: Session, client_id: str, info: dict[str, Any]) -> None:
    session.merge(OAuthClientRow(client_id=client_id, info=info, created_at=now()))


def get_client(session: Session, client_id: str) -> dict[str, Any] | None:
    row = session.get(OAuthClientRow, client_id)
    return row.info if row else None


@transactional
def create_request(
    session: Session,
    *,
    client_id: str,
    redirect_uri: str,
    redirect_uri_provided_explicitly: bool,
    code_challenge: str,
    state: str | None,
    scopes: Sequence[str],
    resource: str | None,
) -> str:
    """Records a sign-in that waits for approval and returns its id. Expired requests and
    codes are removed on the way, so abandoned sign-ins do not pile up."""
    current = now()
    session.execute(delete(OAuthRequestRow).where(col(OAuthRequestRow.expires_at) <= current))
    row = OAuthRequestRow(
        id=secrets.token_urlsafe(24),
        client_id=client_id,
        redirect_uri=redirect_uri,
        redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
        code_challenge=code_challenge,
        state=state,
        scopes=list(scopes),
        resource=resource,
        created_at=current,
        expires_at=current + REQUEST_TTL,
    )
    session.add(row)
    session.flush()
    return row.id


def pending_request(session: Session, request_id: str) -> OAuthRequestInfo:
    row = _waiting(session, request_id)
    return OAuthRequestInfo(
        id=row.id,
        client_name=_client_name(session, row.client_id),
        redirect_uri=row.redirect_uri,
        expires_at=row.expires_at,
    )


@transactional
def approve_request(
    session: Session,
    request_id: str,
    project_ids: Sequence[str],
    permissions: Mapping[str, str] | None = None,
    project_permissions: Mapping[str, Mapping[str, str]] | None = None,
    owner_user_id: str | None = None,
) -> str:
    """Gives the request a one-time code for the chosen projects and returns the URL that
    takes the browser back to the client. `permissions` is one set for every project and
    `project_permissions` a set for single ones; a project neither covers gets full
    access. With `owner_user_id` a user approves: only for their own projects, and the
    token will belong to them."""
    row = _waiting(session, request_id)
    chosen = normalize_list(project_ids)
    if not chosen:
        raise BadRequest("Select at least one project")
    require_projects(session, chosen)
    tokens.assert_owner_reaches(session, owner_user_id, chosen, by_owner=True)
    granted = policy.assign(chosen, permissions, project_permissions)
    code = secrets.token_urlsafe(32)
    row.code_hash = _hash(code)
    row.project_ids = chosen
    row.permissions = granted
    row.owner_user_id = owner_user_id
    row.expires_at = now() + CODE_TTL
    session.flush()
    return _redirect(row.redirect_uri, code=code, state=row.state)


@transactional
def deny_request(session: Session, request_id: str) -> str:
    row = _waiting(session, request_id)
    url = _redirect(row.redirect_uri, error="access_denied", state=row.state)
    session.delete(row)
    session.flush()
    return url


def load_code(session: Session, code: str) -> OAuthRequestRow | None:
    """The approved request behind an unexpired code."""
    return session.exec(
        select(OAuthRequestRow).where(
            col(OAuthRequestRow.code_hash) == _hash(code), col(OAuthRequestRow.expires_at) > now()
        )
    ).first()


@transactional
def redeem_code(session: Session, code: str) -> CreatedApiToken | None:
    """Spends the code and creates the token it stands for, in one transaction: a code
    works once, and a token that cannot be created leaves the code unspent."""
    spent = session.execute(
        delete(OAuthRequestRow)
        .where(
            col(OAuthRequestRow.code_hash) == _hash(code), col(OAuthRequestRow.expires_at) > now()
        )
        .returning(
            OAuthRequestRow.client_id,
            OAuthRequestRow.project_ids,
            OAuthRequestRow.permissions,
            OAuthRequestRow.owner_user_id,
        )
    ).first()
    if spent is None:
        return None
    client_id, project_ids, granted, owner_user_id = spent
    name = f"{_client_name(session, client_id)} (OAuth)"
    # `create_token` commits the session, and with it the delete above.
    return tokens.create_token(
        session, name, project_ids, project_permissions=granted, owner_user_id=owner_user_id
    )
