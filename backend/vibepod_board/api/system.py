from fastapi import APIRouter, Response

from vibepod_board import permissions
from vibepod_board.api.models import LoginRequest, PasswordChange
from vibepod_board.api.responses import AuthState, Items
from vibepod_board.auth import LoginDep, SessionsDep, Unauthorized
from vibepod_board.db import SessionDep
from vibepod_board.errors import Forbidden
from vibepod_board.schemas import PermissionArea
from vibepod_board.services import users

router = APIRouter(prefix="/api", tags=["system"])

# Every tool the MCP server registers; a test keeps this in step with `tools/list`.
MCP_INFO_TOOLS = (
    "add_idea_dependency",
    "add_run_report",
    "answer_task_question",
    "archive_board_card",
    "archive_project",
    "cancel_task_review",
    "cancel_task_run",
    "claim_next_task",
    "create_document",
    "create_idea",
    "create_project",
    "delete_idea",
    "delete_project",
    "get_board_card",
    "get_idea",
    "hand_over_task",
    "link_pull_request",
    "list_archived_cards",
    "list_board",
    "list_documents",
    "list_idea_readiness",
    "list_ideas",
    "list_projects",
    "list_readiness",
    "list_run_reports",
    "list_task_history",
    "list_task_reviews",
    "list_work_order",
    "list_workers",
    "mark_idea_ready",
    "move_board_card",
    "open_pull_request",
    "pause_automation",
    "pull_github_issue",
    "push_github_issue",
    "register_worker",
    "release_task",
    "remove_idea_dependency",
    "remove_offline_workers",
    "remove_worker",
    "renew_task_claim",
    "renew_task_review",
    "request_task_rework",
    "restore_project",
    "resume_automation",
    "set_card_readiness",
    "set_idea_dependencies",
    "set_idea_readiness",
    "sign_off_worker",
    "stop_worker",
    "submit_review",
    "unarchive_board_card",
    "update_board_card",
    "update_board_cards",
    "update_document",
    "update_idea",
    "update_ideas",
    "update_project_settings",
    "upsert_github_issue",
    "worker_heartbeat",
)

@router.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "service": "vibepod-board", "mcp": "/mcp"}


def _admin_state(username: str) -> AuthState:
    return AuthState(
        authenticated=True, username=username, role="admin", must_change_password=False
    )


def _user_state(user: users.SignedInUser) -> AuthState:
    return AuthState(
        authenticated=True,
        username=user.username,
        role="user",
        must_change_password=user.must_change_password,
    )


@router.post("/auth/login")
def login(
    body: LoginRequest, response: Response, sessions: SessionsDep, session: SessionDep
) -> AuthState:
    """Signs in the admin or a user. `role` says which; `mustChangePassword` is true for a
    user with a temporary password, who can do nothing but `POST /api/auth/password`."""
    opened = sessions.login(body.username, body.password)
    if opened is not None:
        response.headers["Set-Cookie"] = opened.cookie
        return _admin_state(opened.username)
    user = users.authenticate(session, body.username, body.password)
    if user is None:
        raise Unauthorized("Invalid username or password")
    response.headers["Set-Cookie"] = sessions.open_for_user(user.id, user.username).cookie
    return _user_state(user)


@router.post("/auth/logout")
def logout(response: Response, sessions: SessionsDep, current: LoginDep) -> AuthState:
    if current:
        sessions.logout(current.id)
    response.headers["Set-Cookie"] = sessions.clear_cookie
    return AuthState(authenticated=False)


@router.get("/auth/me")
def me(current: LoginDep, session: SessionDep) -> AuthState:
    if current is None:
        return AuthState(authenticated=False)
    if current.user_id is None:
        return _admin_state(current.username)
    user = users.signed_in(session, current.user_id)
    if user is None:
        return AuthState(authenticated=False)
    return _user_state(user)


@router.post("/auth/password")
def change_password(
    body: PasswordChange, sessions: SessionsDep, current: LoginDep, session: SessionDep
) -> AuthState:
    """A user sets their own password: after a temporary one, or whenever they like. The
    user's other sessions end. The admin's password lives in the server configuration."""
    if current is None:
        raise Unauthorized("Authentication required")
    if current.user_id is None:
        raise Forbidden("The admin password is set in the server configuration")
    user = users.signed_in(session, current.user_id)
    if user is None:
        raise Unauthorized("Authentication required")
    users.change_password(session, user.id, body.current_password, body.new_password)
    sessions.end_user_sessions(user.id, keep=current.id)
    return AuthState(
        authenticated=True, username=user.username, role="user", must_change_password=False
    )


@router.get("/permission-areas")
def permission_areas() -> Items[PermissionArea]:
    """The areas a token's permissions cover, with the levels each offers and the MCP tools
    a level unlocks. Needs no login: the forms that choose permissions read it."""
    return Items(items=permissions.area_catalog())


@router.get("/mcp-info")
def mcp_info() -> dict[str, object]:
    return {
        "endpoint": "/mcp",
        "transport": "streamable-http",
        "auth": "bearer",
        "tools": list(MCP_INFO_TOOLS),
        "resources": ["vibepod-board://state"],
    }
