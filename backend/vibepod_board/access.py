"""Who is calling: the admin (full access) or an API token scoped to a set of projects,
with a permission level per area in each of them.

A token's permissions are applied by narrowing: before a route or tool runs,
`for_operation` reduces `project_ids` to the projects whose permissions cover it. The
services scope everything they read and write by `project_ids`, so they need to know
nothing about permissions.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace

from vibepod_board import permissions as policy
from vibepod_board.errors import Forbidden
from vibepod_board.permissions import FULL_ACCESS, Needs, Requirement


@dataclass(frozen=True)
class AdminAccess:
    username: str
    kind: str = field(default="admin", init=False)


@dataclass(frozen=True)
class TokenAccess:
    token_id: str
    # The projects the caller may use for the operation at hand: every mapped project,
    # until `for_operation` narrows them.
    project_ids: tuple[str, ...]
    # Every mapped project with its level per area; see `vibepod_board.permissions`.
    permissions: Mapping[str, Mapping[str, str]] = field(default_factory=dict, hash=False)
    # What the operation at hand needs, once the projects were narrowed to it.
    needs: tuple[Requirement, ...] = ()
    kind: str = field(default="token", init=False)


AccessContext = AdminAccess | TokenAccess


def admin_access(username: str) -> AdminAccess:
    return AdminAccess(username)


def token_access(
    token_id: str,
    project_ids: list[str] | tuple[str, ...],
    permissions: Mapping[str, Mapping[str, str]] | None = None,
) -> TokenAccess:
    """`permissions` holds a set per project; without it the token has full access to
    every project. A project the sets do not name has no access."""
    if permissions is None:
        permissions = {project_id: FULL_ACCESS for project_id in project_ids}
    return TokenAccess(token_id, tuple(project_ids), dict(permissions))


def for_operation(access: TokenAccess, needs: Needs | Sequence[Requirement] | None) -> TokenAccess:
    """The token's access to one operation: its projects, narrowed to those whose
    permissions cover everything the operation needs. Refuses when no project does, and
    when the policy does not list the operation (`None`)."""
    if needs is None:
        raise Forbidden("This token cannot use this operation")
    required = policy.requirements(needs)
    if not required or not access.project_ids:
        # Open to every token, or a token without projects: nothing to narrow. Such a
        # token sees empty lists, and the services refuse its writes.
        return access
    allowed = tuple(
        project_id
        for project_id in access.project_ids
        if policy.missing(access.permissions.get(project_id, {}), required) is None
    )
    if not allowed:
        lacking = next(
            (
                found
                for project_id in access.project_ids
                if (found := policy.missing(access.permissions.get(project_id, {}), required))
            ),
            required[0],
        )
        raise Forbidden(policy.denial(lacking))
    return replace(access, project_ids=allowed, needs=required)


def is_admin(access: AccessContext) -> bool:
    return isinstance(access, AdminAccess)


def before_narrowing(access: AccessContext) -> TokenAccess | None:
    """The token with every mapped project again, when `for_operation` left some out, and
    `None` when it left none out. A lookup uses it to tell a record the permissions keep
    out of reach from one that does not exist."""
    if isinstance(access, TokenAccess) and len(access.project_ids) < len(access.permissions):
        return replace(access, project_ids=tuple(access.permissions), needs=())
    return None


def assert_can_access_project(access: AccessContext, project_id: str) -> None:
    if isinstance(access, AdminAccess) or project_id in access.project_ids:
        return
    if project_id in access.permissions:
        # Mapped to the project, but its permissions do not cover the operation.
        lacking = policy.missing(access.permissions[project_id], access.needs)
        if lacking is not None:
            raise Forbidden(f"{policy.denial(lacking)} in project: {project_id}")
    raise Forbidden(f"Token is not allowed to access project: {project_id}")


def require_admin(access: AccessContext) -> None:
    if not isinstance(access, AdminAccess):
        raise Forbidden("Admin access required")


def default_project_id_for_create(
    access: AccessContext, requested: str | None, archived: Collection[str] = ()
) -> str | None:
    """Admins may leave the project open; a token falls back to its first mapped project
    that is not archived."""
    if isinstance(access, AdminAccess):
        return requested
    if requested:
        assert_can_access_project(access, requested)
        return requested
    if not access.project_ids:
        raise Forbidden("Token is not mapped to any projects")
    active = [project_id for project_id in access.project_ids if project_id not in archived]
    if not active:
        raise Forbidden("Token is not mapped to any active projects")
    return active[0]
