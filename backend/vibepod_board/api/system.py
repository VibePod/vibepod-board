from typing import Annotated

from fastapi import APIRouter, Depends, Response

from vibepod_board.api.models import LoginRequest
from vibepod_board.api.responses import AuthState
from vibepod_board.auth import (
    AdminSession,
    SessionsDep,
    Unauthorized,
    current_admin_session,
)

router = APIRouter(prefix="/api", tags=["system"])

# Every tool the MCP server registers; a test keeps this in step with `tools/list`.
MCP_INFO_TOOLS = (
    "add_idea_dependency",
    "archive_board_card",
    "create_document",
    "create_idea",
    "create_project",
    "delete_idea",
    "get_board_card",
    "get_idea",
    "list_archived_cards",
    "list_board",
    "list_documents",
    "list_idea_readiness",
    "list_ideas",
    "list_projects",
    "list_readiness",
    "list_work_order",
    "mark_idea_ready",
    "move_board_card",
    "pull_github_issue",
    "push_github_issue",
    "remove_idea_dependency",
    "set_card_readiness",
    "set_idea_dependencies",
    "set_idea_readiness",
    "unarchive_board_card",
    "update_board_card",
    "update_board_cards",
    "update_document",
    "update_idea",
    "update_ideas",
    "upsert_github_issue",
)

AdminSessionDep = Annotated[AdminSession | None, Depends(current_admin_session)]


@router.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "service": "vibepod-board", "mcp": "/mcp"}


@router.post("/auth/login")
def login(body: LoginRequest, response: Response, sessions: SessionsDep) -> AuthState:
    session = sessions.login(body.username, body.password)
    if session is None:
        raise Unauthorized("Invalid username or password")
    response.headers["Set-Cookie"] = session.cookie
    return AuthState(authenticated=True, username=session.username)


@router.post("/auth/logout")
def logout(response: Response, sessions: SessionsDep, admin: AdminSessionDep) -> AuthState:
    if admin:
        sessions.logout(admin.id)
    response.headers["Set-Cookie"] = sessions.clear_cookie
    return AuthState(authenticated=False)


@router.get("/auth/me")
def me(admin: AdminSessionDep) -> AuthState:
    if admin is None:
        return AuthState(authenticated=False)
    return AuthState(authenticated=True, username=admin.username)


@router.get("/mcp-info")
def mcp_info() -> dict[str, object]:
    return {
        "endpoint": "/mcp",
        "transport": "streamable-http",
        "auth": "bearer",
        "tools": list(MCP_INFO_TOOLS),
        "resources": ["vibepod-board://state"],
    }
