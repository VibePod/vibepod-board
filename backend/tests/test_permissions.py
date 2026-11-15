"""Token permissions: how a permission set is normalised and checked, and the policy tables."""

import pytest

from vibepod_board import permissions
from vibepod_board.errors import BadRequest, Forbidden
from vibepod_board.permissions import FULL_ACCESS, Area, Level, Requirement

NO_ACCESS = dict.fromkeys(FULL_ACCESS, "none")


def test_full_access_is_write_in_every_area() -> None:
    assert FULL_ACCESS == {area.value: "write" for area in Area}


def test_a_token_without_a_permission_set_gets_full_access() -> None:
    assert permissions.normalize(None) == FULL_ACCESS


def test_areas_a_permission_set_does_not_name_get_no_access() -> None:
    assert permissions.normalize({"tasks": "read", "documents": "write"}) == {
        **NO_ACCESS,
        "tasks": "read",
        "documents": "write",
    }


def test_an_unknown_area_is_refused() -> None:
    with pytest.raises(BadRequest, match="Unknown permission area: billing"):
        permissions.normalize({"billing": "read"})


def test_an_unknown_level_is_refused() -> None:
    with pytest.raises(BadRequest, match="Unknown permission level for tasks: admin"):
        permissions.normalize({"tasks": "admin"})


def test_an_area_refuses_a_level_it_does_not_offer() -> None:
    # Nothing in the project area can only be read.
    with pytest.raises(BadRequest, match="Unknown permission level for project: read"):
        permissions.normalize({"project": "read"})


@pytest.mark.parametrize(
    ("granted", "needed", "allowed"),
    [
        ("write", Level.WRITE, True),
        ("write", Level.READ, True),
        ("read", Level.READ, True),
        ("read", Level.WRITE, False),
        ("none", Level.READ, False),
    ],
)
def test_a_level_covers_the_levels_below_it(granted: str, needed: Level, allowed: bool) -> None:
    assert permissions.allows({"tasks": granted}, Requirement(Area.TASKS, needed)) is allowed


def test_an_area_missing_from_the_set_is_denied() -> None:
    assert not permissions.allows({}, Requirement(Area.BOARD, Level.READ))


def test_an_operation_that_needs_no_permission_is_always_allowed() -> None:
    permissions.require(NO_ACCESS, permissions.OPEN)


def test_a_denied_operation_names_the_level_and_the_area() -> None:
    with pytest.raises(Forbidden, match="This token has no write access to Tasks"):
        permissions.require({"tasks": "read"}, Requirement(Area.TASKS, Level.WRITE))


def test_an_operation_missing_from_the_policy_is_refused() -> None:
    with pytest.raises(Forbidden, match="This token cannot use this operation"):
        permissions.require(FULL_ACCESS, None)


def test_tools_and_routes_name_the_permission_they_need() -> None:
    assert permissions.TOOL_PERMISSIONS["list_ideas"] == Requirement(Area.TASKS, Level.READ)
    assert permissions.TOOL_PERMISSIONS["claim_next_task"] == Requirement(
        Area.AUTOMATION, Level.WRITE
    )
    assert permissions.TOOL_PERMISSIONS["list_projects"] is permissions.OPEN
    assert permissions.ROUTE_PERMISSIONS["PATCH /api/ideas/{idea_id}"] == Requirement(
        Area.TASKS, Level.WRITE
    )
    assert permissions.ROUTE_PERMISSIONS["GET /api/projects"] is permissions.OPEN


def test_the_catalog_describes_every_area_with_its_levels_and_tools() -> None:
    catalog = {area.key: area for area in permissions.area_catalog()}

    assert list(catalog) == [area.value for area in Area]
    assert catalog["tasks"].label == "Tasks"
    assert catalog["tasks"].levels == ["none", "read", "write"]
    assert catalog["project"].levels == ["none", "write"]
    assert "list_ideas" in catalog["tasks"].read_tools
    assert "create_idea" in catalog["tasks"].write_tools
    assert all(area.description for area in catalog.values())
