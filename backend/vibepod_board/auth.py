"""Admin sessions (in memory, lost on restart) and bearer-token authentication."""

import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from vibepod_board import permissions
from vibepod_board.access import (
    AccessContext,
    AdminAccess,
    TokenAccess,
    admin_access,
    token_access,
)
from vibepod_board.db import SessionDep
from vibepod_board.errors import BoardError, Forbidden
from vibepod_board.services import tokens

SESSION_COOKIE = "vibepod_session"
COOKIE_ATTRIBUTES = "HttpOnly; SameSite=Lax; Path=/"


class Unauthorized(BoardError):
    status_code = 401


@dataclass(frozen=True)
class AdminSession:
    id: str
    username: str
    created_at: float

    @property
    def cookie(self) -> str:
        return f"{SESSION_COOKIE}={self.id}; {COOKIE_ATTRIBUTES}"


class AdminSessionManager:
    clear_cookie = f"{SESSION_COOKIE}=; {COOKIE_ATTRIBUTES}; Max-Age=0"

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._sessions: dict[str, AdminSession] = {}

    def login(self, username: str, password: str) -> AdminSession | None:
        # Both comparisons always run, so timing does not reveal which one failed.
        username_ok = hmac.compare_digest(username.encode(), self._username.encode())
        password_ok = hmac.compare_digest(password.encode(), self._password.encode())
        if not (username_ok and password_ok):
            return None
        session = AdminSession(secrets.token_urlsafe(32), username, time.time())
        self._sessions[session.id] = session
        return session

    def authenticate(self, session_id: str | None) -> AdminSession | None:
        return self._sessions.get(session_id) if session_id else None

    def authenticate_cookie(self, cookie_header: str | None) -> AdminSession | None:
        for part in (cookie_header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return self.authenticate(value)
        return None

    def logout(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


# Declares bearer auth in the OpenAPI spec, so Swagger UI offers "Authorize". It never
# rejects a request itself: `resolve_access` still reads the header, after the admin cookie.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Project-scoped API token, created by an admin under API Tokens. A route "
    "outside the token's permissions answers 403, and so do the admin-only routes, which "
    "need the admin session cookie.",
)


def parse_bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization[len("Bearer ") :].strip() or None


def get_sessions(request: Request) -> AdminSessionManager:
    return request.app.state.sessions


SessionsDep = Annotated[AdminSessionManager, Depends(get_sessions)]


def current_admin_session(request: Request, sessions: SessionsDep) -> AdminSession | None:
    return sessions.authenticate(request.cookies.get(SESSION_COOKIE))


def resolve_access(
    session: Session, admin: AdminSession | None, authorization: str | None
) -> AccessContext | None:
    if admin:
        return admin_access(admin.username)
    token = parse_bearer_token(authorization)
    if not token:
        return None
    authenticated = tokens.authenticate_token(session, token)
    if not authenticated:
        return None
    return token_access(
        authenticated.token_id, authenticated.project_ids, authenticated.permissions
    )


def require_access(
    request: Request,
    session: SessionDep,
    admin: Annotated[AdminSession | None, Depends(current_admin_session)],
    _bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AccessContext:
    access = resolve_access(session, admin, request.headers.get("authorization"))
    if access is None:
        raise Unauthorized("Authentication required")
    return access


def require_permitted_access(
    request: Request,
    access: Annotated[AccessContext, Depends(require_access)],
) -> AccessContext:
    """The caller, once a token's permissions cover the route. The policy names what each
    route needs; a route it does not list is refused for every token."""
    if isinstance(access, TokenAccess):
        route = request.scope.get("route")
        needs = permissions.ROUTE_PERMISSIONS.get(f"{request.method} {getattr(route, 'path', '')}")
        permissions.require(access.permissions, needs)
    return access


def require_admin_access(
    access: Annotated[AccessContext, Depends(require_access)],
) -> AdminAccess:
    if not isinstance(access, AdminAccess):
        raise Forbidden("Admin access required")
    return access


AccessDep = Annotated[AccessContext, Depends(require_permitted_access)]
AdminDep = Annotated[AdminAccess, Depends(require_admin_access)]
