"""References: a task by id, key such as VP-236 or bare number; a project by id, key or title."""

import pytest
from sqlmodel import Session

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import BoardColumn
from vibepod_board.errors import BadRequest, NotFound
from vibepod_board.graph import format_task_key
from vibepod_board.services import board, dependencies, ideas, projects
from vibepod_board.services.references import TaskKey, TaskNumber, parse_task_reference

ADMIN = admin_access("admin")


@pytest.fixture
def vp(session: Session):
    return projects.create_project(session, "VP", "VibePod")


def test_parses_a_project_key_and_task_number() -> None:
    assert parse_task_reference("VP-236") == TaskKey("VP", 236)
    assert parse_task_reference("  vp-236 ") == TaskKey("VP", 236)
    assert parse_task_reference("236") == TaskNumber(236)
    assert parse_task_reference(format_task_key("VP", 236)) == TaskKey("VP", 236)


@pytest.mark.parametrize(
    "value",
    ["0484e578-aab9-4678-9301-7ebb2d67de5b", "VP-236-2", "SVEN-1", "VP-0", "VP-01", ""],
)
def test_treats_anything_else_as_an_opaque_id(value: str) -> None:
    assert parse_task_reference(value) is None


def test_resolves_a_task_by_id_key_and_bare_number(session: Session, vp) -> None:
    task = ideas.create_idea(session, ADMIN, title="Collapse the write", project_id=vp.id)
    scoped = token_access("token-1", [vp.id])

    by_id = ideas.update_idea(session, ADMIN, task.id, summary="one")
    by_key = ideas.update_idea(session, ADMIN, f"VP-{task.task_number}", summary="two")
    by_lower = ideas.update_idea(session, ADMIN, f"vp-{task.task_number}", summary="three")
    by_number = ideas.update_idea(session, scoped, str(task.task_number), summary="four")

    assert {by_id.id, by_key.id, by_lower.id, by_number.id} == {task.id}
    assert by_number.summary == "four"


def test_resolves_a_project_by_id_key_and_title(session: Session, vp) -> None:
    ideas.create_idea(session, ADMIN, title="Only task", project_id=vp.id)
    for reference in ("VP", "vp", "VibePod", "vibepod", vp.id):
        assert len(ideas.list_ideas(session, ADMIN, reference)) == 1


def test_resolves_the_project_of_a_new_task_by_key(session: Session, vp) -> None:
    created = ideas.create_idea(session, ADMIN, title="Filed by key", project_id="VP")
    assert created.project_id == vp.id
    assert created.task_number == 1


def test_a_token_files_a_task_by_project_key(session: Session, vp) -> None:
    created = ideas.create_idea(
        session, token_access("token-1", [vp.id]), title="By key", project_id="vp"
    )
    assert created.project_id == vp.id


def test_refuses_a_bare_number_when_several_projects_are_in_scope(session: Session, vp) -> None:
    other = projects.create_project(session, "OPS", "Operations")
    ideas.create_idea(session, ADMIN, title="First", project_id=vp.id)
    ideas.create_idea(session, ADMIN, title="Second", project_id=other.id)

    with pytest.raises(BadRequest, match="Ambiguous task reference: 1 matches 2 projects"):
        ideas.update_idea(session, ADMIN, "1", summary="x")
    # A token covering one project has no ambiguity.
    scoped = token_access("token-1", [vp.id])
    assert ideas.update_idea(session, scoped, "1", summary="x").project_id == vp.id


def test_refuses_an_ambiguous_project_title(session: Session) -> None:
    projects.create_project(session, "BRD", "Board")
    projects.create_project(session, "BDX", "Board")
    with pytest.raises(
        BadRequest, match=r"Ambiguous project reference: Board matches 2 projects \(BDX, BRD\)"
    ):
        ideas.list_ideas(session, ADMIN, "Board")


def test_reports_a_foreign_task_as_not_found(session: Session, vp) -> None:
    other = projects.create_project(session, "OPS", "Operations")
    foreign = ideas.create_idea(session, ADMIN, title="Hidden", project_id=other.id)
    scoped = token_access("token-1", [vp.id])

    with pytest.raises(NotFound, match=f"Task not found: {foreign.id}"):
        ideas.update_idea(session, scoped, foreign.id, summary="x")
    with pytest.raises(NotFound, match="Task not found: OPS-1"):
        ideas.update_idea(session, scoped, "OPS-1", summary="x")
    with pytest.raises(NotFound, match="Task not found: 1"):
        ideas.get_idea(session, scoped, "1")


def test_names_the_project_and_number_when_a_key_does_not_resolve(session: Session, vp) -> None:
    with pytest.raises(NotFound, match=r"Task not found: VP-999 \(project VP, task 999\)"):
        ideas.update_idea(session, ADMIN, "VP-999", summary="x")


def test_resolves_a_board_card_by_task_key(session: Session, vp) -> None:
    task = ideas.create_idea(session, ADMIN, title="On the board", project_id=vp.id)
    ideas.mark_ready(session, ADMIN, task.id)

    moved = board.move_card(session, ADMIN, f"VP-{task.task_number}", BoardColumn.PLANNED)
    assert moved.idea_id == task.id
    assert moved.column == BoardColumn.PLANNED
    assert board.get_card(session, ADMIN, f"vp-{task.task_number}").id == moved.id


def test_reports_a_task_without_a_card_as_card_not_found(session: Session, vp) -> None:
    task = ideas.create_idea(session, ADMIN, title="Not ready", project_id=vp.id)
    with pytest.raises(NotFound, match="Board card not found: VP-1"):
        board.get_card(session, ADMIN, f"VP-{task.task_number}")


def test_accepts_keys_as_dependency_arguments(session: Session, vp) -> None:
    blocker = ideas.create_idea(session, ADMIN, title="Blocker", project_id=vp.id)
    waiting = ideas.create_idea(session, ADMIN, title="Waiting", project_id=vp.id)

    updated = dependencies.set_dependencies(
        session, ADMIN, f"VP-{waiting.task_number}", [f"VP-{blocker.task_number}"]
    )
    assert updated.depends_on == [blocker.id]

    removed = dependencies.remove_dependency(
        session, ADMIN, waiting.id, f"VP-{blocker.task_number}"
    )
    assert removed.depends_on == []


def test_still_reports_an_unparseable_dependency_as_not_found(session: Session, vp) -> None:
    task = ideas.create_idea(session, ADMIN, title="Waiting", project_id=vp.id)
    with pytest.raises(NotFound, match="Task not found: missing-task"):
        dependencies.set_dependencies(session, ADMIN, task.id, ["missing-task"])
