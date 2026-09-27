"""Port of tests/access.test.ts. `filterProjectIds` has no Python equivalent, so its
assertions are dropped."""

import pytest

from vibepod_board.access import (
    admin_access,
    assert_can_access_project,
    default_project_id_for_create,
    token_access,
)
from vibepod_board.errors import Forbidden


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
