"""Who is calling: the admin (full access) or an API token scoped to a set of projects."""

from dataclasses import dataclass, field

from vibepod_board.errors import Forbidden


@dataclass(frozen=True)
class AdminAccess:
    username: str
    kind: str = field(default="admin", init=False)


@dataclass(frozen=True)
class TokenAccess:
    token_id: str
    project_ids: tuple[str, ...]
    kind: str = field(default="token", init=False)


AccessContext = AdminAccess | TokenAccess


def admin_access(username: str) -> AdminAccess:
    return AdminAccess(username)


def token_access(token_id: str, project_ids: list[str] | tuple[str, ...]) -> TokenAccess:
    return TokenAccess(token_id, tuple(project_ids))


def is_admin(access: AccessContext) -> bool:
    return isinstance(access, AdminAccess)


def assert_can_access_project(access: AccessContext, project_id: str) -> None:
    if isinstance(access, AdminAccess) or project_id in access.project_ids:
        return
    raise Forbidden(f"Token is not allowed to access project: {project_id}")


def require_admin(access: AccessContext) -> None:
    if not isinstance(access, AdminAccess):
        raise Forbidden("Admin access required")


def default_project_id_for_create(access: AccessContext, requested: str | None) -> str | None:
    """Admins may leave the project open; a token falls back to its first mapped project."""
    if isinstance(access, AdminAccess):
        return requested
    if requested:
        assert_can_access_project(access, requested)
        return requested
    if not access.project_ids:
        raise Forbidden("Token is not mapped to any projects")
    return access.project_ids[0]
