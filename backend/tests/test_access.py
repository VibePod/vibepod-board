"""Port of tests/access.test.ts. `filterProjectIds` has no Python equivalent, so its
assertions are dropped."""

import pytest
from sqlmodel import Session

from vibepod_board.access import (
    admin_access,
    assert_can_access_project,
    before_narrowing,
    caller_identity,
    default_project_id_for_create,
    for_operation,
    token_access,
    user_access,
)
from vibepod_board.errors import Forbidden
from vibepod_board.permissions import FULL_ACCESS, OPEN, Area, Level, Requirement

NO_ACCESS = dict.fromkeys(FULL_ACCESS, "none")
WRITE_TASKS = Requirement(Area.TASKS, Level.WRITE)
READ_TASKS = Requirement(Area.TASKS, Level.READ)


def test_lets_admins_access_every_project() -> None:
    access = admin_access("admin")

    assert_can_access_project(access, "outside")


def test_limits_token_access_to_mapped_projects() -> None:
    access = token_access("token-1", ["project-1", "project-2"])

    assert default_project_id_for_create(access, None) == "project-1"
    assert default_project_id_for_create(access, "project-2") == "project-2"
    with pytest.raises(Forbidden, match="Token is not allowed to access project: project-3"):
        default_project_id_for_create(access, "project-3")
    with pytest.raises(Forbidden, match="Token is not allowed to access project: project-3"):
        assert_can_access_project(access, "project-3")


def test_rejects_create_defaults_when_a_token_has_no_projects() -> None:
    with pytest.raises(Forbidden, match="Token is not mapped to any projects"):
        default_project_id_for_create(token_access("token-1", []), None)


def planner() -> object:
    """A token that writes tasks in `app`, only reads them in `api` and sees nothing in `ops`."""
    return token_access(
        "token-1",
        ["app", "api", "ops"],
        {
            "app": {**NO_ACCESS, "tasks": "write"},
            "api": {**NO_ACCESS, "tasks": "read"},
            "ops": NO_ACCESS,
        },
    )


def test_a_token_without_permissions_has_full_access_to_its_projects() -> None:
    access = token_access("token-1", ["app", "api"])

    assert access.permissions == {"app": FULL_ACCESS, "api": FULL_ACCESS}
    assert for_operation(access, WRITE_TASKS).project_ids == ("app", "api")


def test_an_operation_is_narrowed_to_the_projects_whose_permissions_cover_it() -> None:
    assert for_operation(planner(), WRITE_TASKS).project_ids == ("app",)
    assert for_operation(planner(), READ_TASKS).project_ids == ("app", "api")


def test_an_operation_that_needs_no_permission_keeps_every_project() -> None:
    assert for_operation(planner(), OPEN).project_ids == ("app", "api", "ops")


def test_an_operation_needs_every_requirement_in_the_same_project() -> None:
    needs = (READ_TASKS, Requirement(Area.BOARD, Level.READ))
    access = token_access(
        "token-1",
        ["app", "api"],
        {
            "app": {**NO_ACCESS, "tasks": "read"},
            "api": {**NO_ACCESS, "tasks": "read", "board": "read"},
        },
    )

    assert for_operation(access, needs).project_ids == ("api",)


def test_an_operation_no_project_allows_is_refused() -> None:
    with pytest.raises(Forbidden, match="This token has no write access to Board$"):
        for_operation(planner(), Requirement(Area.BOARD, Level.WRITE))


def test_an_operation_the_policy_does_not_list_is_refused() -> None:
    with pytest.raises(Forbidden, match="This token cannot use this operation"):
        for_operation(planner(), None)


def test_a_narrowed_token_names_the_permission_a_mapped_project_lacks() -> None:
    access = for_operation(planner(), WRITE_TASKS)

    assert_can_access_project(access, "app")
    with pytest.raises(Forbidden, match="This token has no write access to Tasks in project: api"):
        assert_can_access_project(access, "api")
    with pytest.raises(Forbidden, match="Token is not allowed to access project: other"):
        assert_can_access_project(access, "other")


def test_a_new_task_defaults_to_the_first_project_the_token_may_write_to() -> None:
    access = token_access(
        "token-1",
        ["app", "api"],
        {"app": {**NO_ACCESS, "tasks": "read"}, "api": {**NO_ACCESS, "tasks": "write"}},
    )

    assert default_project_id_for_create(for_operation(access, WRITE_TASKS), None) == "api"


def test_a_token_without_projects_has_nothing_to_narrow() -> None:
    # It sees empty lists, and a write tells it that it is mapped to no project.
    orphan = token_access("token-1", [])

    assert for_operation(orphan, WRITE_TASKS).project_ids == ()
    with pytest.raises(Forbidden, match="Token is not mapped to any projects"):
        default_project_id_for_create(for_operation(orphan, WRITE_TASKS), None)


def test_limits_a_user_to_the_assigned_projects() -> None:
    access = user_access("user-1", "rebecca", ["project-1", "project-2"])

    assert access.kind == "user"
    assert access.project_ids == ("project-1", "project-2")
    assert default_project_id_for_create(access, None) == "project-1"
    assert default_project_id_for_create(access, "project-2") == "project-2"
    assert_can_access_project(access, "project-2")
    with pytest.raises(Forbidden, match="You have no access to project: outside"):
        assert_can_access_project(access, "outside")
    with pytest.raises(Forbidden, match="You have no access to project: outside"):
        default_project_id_for_create(access, "outside")


def test_a_user_skips_archived_projects_when_no_project_is_named() -> None:
    access = user_access("user-1", "rebecca", ["old", "current"])

    assert default_project_id_for_create(access, None, archived={"old"}) == "current"
    with pytest.raises(Forbidden, match="You are not assigned to any active projects"):
        default_project_id_for_create(access, None, archived={"old", "current"})


def test_rejects_create_defaults_when_a_user_has_no_projects() -> None:
    with pytest.raises(Forbidden, match="You are not assigned to any projects"):
        default_project_id_for_create(user_access("user-1", "rebecca", []), None)


def test_a_user_is_never_narrowed_like_a_token() -> None:
    assert before_narrowing(user_access("user-1", "rebecca", ["project-1"])) is None


def test_names_each_kind_of_caller_apart() -> None:
    assert caller_identity(admin_access("admin")) == "admin:admin"
    assert caller_identity(token_access("token-1", [])) == "token:token-1"
    assert caller_identity(user_access("user-1", "rebecca", [])) == "user:user-1"


def test_the_history_names_a_user_by_username(session: Session) -> None:
    from vibepod_board.services.history import actor_for

    assert actor_for(session, user_access("user-1", "rebecca", [])) == "rebecca"
    assert actor_for(session, admin_access("admin")) == "admin"
