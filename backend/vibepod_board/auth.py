"""Browser sessions of the admin and of users (in memory, lost on restart) and
bearer-token authentication."""

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
    UserAccess,
    admin_access,
    for_operation,
    token_access,
    user_access,
)
from vibepod_board.db import SessionDep
from vibepod_board.errors import BoardError, Forbidden
from vibepod_board.services import tokens, users

SESSION_COOKIE = "vibepod_session"
COOKIE_ATTRIBUTES = "HttpOnly; SameSite=Lax; Path=/"
# What a user with a temporary password is told by every protected route.
PASSWORD_CHANGE_REQUIRED = "Set a new password first"


class Unauthorized(BoardError):
    status_code = 401


@dataclass(frozen=True)
class LoginSession:
    id: str
    username: str
    created_at: float
    # The user the session belongs to; None for the admin.
    user_id: str | None = None

    @property
    def cookie(self) -> str:
        return f"{SESSION_COOKIE}={self.id}; {COOKIE_ATTRIBUTES}"


class SessionManager:
    """The open sessions. The admin's credentials come from the environment and are
    checked here; a user's password is checked by `services.users` before a session is
    opened for them."""

    clear_cookie = f"{SESSION_COOKIE}=; {COOKIE_ATTRIBUTES}; Max-Age=0"

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._sessions: dict[str, LoginSession] = {}

    def _open(self, username: str, user_id: str | None) -> LoginSession:
        session = LoginSession(secrets.token_urlsafe(32), username, time.time(), user_id)
        self._sessions[session.id] = session
        return session

    def login(self, username: str, password: str) -> LoginSession | None:
        """Signs the admin in; None when the credentials are not the admin's."""
        # Both comparisons always run, so timing does not reveal which one failed.
        username_ok = hmac.compare_digest(username.encode(), self._username.encode())
        password_ok = hmac.compare_digest(password.encode(), self._password.encode())
        if not (username_ok and password_ok):
            return None
        return self._open(username, None)

    def open_for_user(self, user_id: str, username: str) -> LoginSession:
        """A session for a user whose password was already checked."""
        return self._open(username, user_id)

    def authenticate(self, session_id: str | None) -> LoginSession | None:
        return self._sessions.get(session_id) if session_id else None

    def authenticate_cookie(self, cookie_header: str | None) -> LoginSession | None:
        for part in (cookie_header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return self.authenticate(value)
        return None

    def logout(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def end_user_sessions(self, user_id: str, keep: str | None = None) -> None:
        """Ends every session of the user, except the one named by `keep`."""
        for session in list(self._sessions.values()):
            if session.user_id == user_id and session.id != keep:
                self._sessions.pop(session.id, None)


# Declares bearer auth in the OpenAPI spec, so Swagger UI offers "Authorize". It never
# rejects a request itself: `resolve_access` still reads the header, after the admin cookie.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Project-scoped API token, created under API Tokens. A route outside the "
    "token's permissions answers 403, and so do the routes for the admin and for users, "
    "which need a session cookie.",
)


def parse_bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization[len("Bearer ") :].strip() or None


def get_sessions(request: Request) -> SessionManager:
    return request.app.state.sessions


SessionsDep = Annotated[SessionManager, Depends(get_sessions)]


def current_login(request: Request, sessions: SessionsDep) -> LoginSession | None:
    return sessions.authenticate(request.cookies.get(SESSION_COOKIE))


LoginDep = Annotated[LoginSession | None, Depends(current_login)]


def resolve_access(
    session: Session, login: LoginSession | None, authorization: str | None
) -> AccessContext | None:
    """The caller behind a session cookie or a bearer token. A user is read from the
    database on every request, so a change of projects, a deactivation and a deletion apply
    at once. A user who still has a temporary password is refused here: every protected
    route passes through, so none of them can be reached before the password is set."""
    if login and login.user_id is None:
        return admin_access(login.username)
    if login and login.user_id:
        user = users.signed_in(session, login.user_id)
        if user is not None:
            if user.must_change_password:
                raise Forbidden(PASSWORD_CHANGE_REQUIRED)
            return user_access(user.id, user.username, user.project_ids)
        # Deactivated or deleted since signing in: the cookie counts for nothing.
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
    login: LoginDep,
    _bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AccessContext:
    access = resolve_access(session, login, request.headers.get("authorization"))
    if access is None:
        raise Unauthorized("Authentication required")
    return access


def require_permitted_access(
    request: Request,
    access: Annotated[AccessContext, Depends(require_access)],
) -> AccessContext:
    """The caller, with a token narrowed to the projects whose permissions cover the
    route. The policy names what each route needs; a route it does not list is refused for
    every token, and so is one that none of the token's projects allows."""
    if isinstance(access, TokenAccess):
        route = request.scope.get("route")
        needs = permissions.ROUTE_PERMISSIONS.get(f"{request.method} {getattr(route, 'path', '')}")
        return for_operation(access, needs)
    return access


def require_admin_access(
    access: Annotated[AccessContext, Depends(require_access)],
) -> AdminAccess:
    if not isinstance(access, AdminAccess):
        raise Forbidden("Admin access required")
    return access


def require_member_access(
    access: Annotated[AccessContext, Depends(require_access)],
) -> AdminAccess | UserAccess:
    """Somebody at the board in a browser: the admin or a user. A token is refused,
    whatever its permissions; the routes behind this hand out access themselves."""
    if isinstance(access, TokenAccess):
        raise Forbidden("Sign in to the board for this")
    return access


def acting_user_id(member: AdminAccess | UserAccess) -> str | None:
    """The user a member acts as, or None for the admin, who acts on everybody's records."""
    return member.user_id if isinstance(member, UserAccess) else None


AccessDep = Annotated[AccessContext, Depends(require_permitted_access)]
AdminDep = Annotated[AdminAccess, Depends(require_admin_access)]
MemberDep = Annotated[AdminAccess | UserAccess, Depends(require_member_access)]
