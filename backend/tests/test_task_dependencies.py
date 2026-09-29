"""Port of tests/task-dependencies.test.ts (PostgresBoardStore task dependencies)."""

import pytest
from sqlmodel import Session

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import BoardColumn, IdeaStatus
from vibepod_board.errors import BadRequest, Conflict, NotFound
from vibepod_board.schemas import BoardCard, Idea, Project
from vibepod_board.services import board, dependencies, ideas, projects

ADMIN = admin_access("admin")


def project_with_tasks(
    session: Session, titles: list[str], key: str = "APP"
) -> tuple[Project, list[Idea]]:
    project = projects.create_project(session, key=key, title=f"Project {key}")
    tasks = [
        ideas.create_idea(session, ADMIN, project_id=project.id, title=title) for title in titles
    ]
    return project, tasks


def finish_task(session: Session, idea_id: str) -> BoardCard:
    ideas.mark_ready(session, ADMIN, idea_id)
    card = next(
        (item for item in board.list_cards(session, ADMIN) if item.idea_id == idea_id), None
    )
    if card is None:
        raise AssertionError("Board card was not created")
    return board.move_card(session, ADMIN, card.id, BoardColumn.DONE)


def find(items: list[Idea], idea_id: str) -> Idea:
    return next(item for item in items if item.id == idea_id)


def test_links_tasks_and_reports_both_directions_of_the_graph(session: Session) -> None:
    _, (schema, api) = project_with_tasks(session, ["Schema", "API"])

    linked = dependencies.add_dependency(session, ADMIN, api.id, schema.id)
    assert linked.depends_on == [schema.id]
    assert linked.blocked_by == [schema.id]

    reloaded = ideas.list_ideas(session, ADMIN)
    assert find(reloaded, schema.id).blocks == [api.id]
    assert find(reloaded, schema.id).blocked_by == []


def test_clears_blocked_by_once_the_blocking_task_is_done_or_denied(session: Session) -> None:
    _, (schema, api, docs) = project_with_tasks(session, ["Schema", "API", "Docs"])
    dependencies.set_dependencies(session, ADMIN, api.id, [schema.id, docs.id])

    blocked = ideas.list_ideas(session, ADMIN)
    assert sorted(find(blocked, api.id).blocked_by) == sorted([schema.id, docs.id])

    finish_task(session, schema.id)
    ideas.update_idea(session, ADMIN, docs.id, status=IdeaStatus.DENIED)

    unblocked = ideas.list_ideas(session, ADMIN)
    assert find(unblocked, api.id).blocked_by == []
    assert sorted(find(unblocked, api.id).depends_on) == sorted([schema.id, docs.id])


def test_mirrors_dependencies_onto_the_linked_board_card(session: Session) -> None:
    _, (schema, api) = project_with_tasks(session, ["Schema", "API"])
    dependencies.add_dependency(session, ADMIN, api.id, schema.id)
    ideas.mark_ready(session, ADMIN, api.id)

    columns = board.board_columns(session, ADMIN)
    card = next((item for item in columns.ready if item.idea_id == api.id), None)
    assert card is not None
    assert card.depends_on == [schema.id]
    assert card.blocked_by == [schema.id]

    finish_task(session, schema.id)
    after_columns = board.board_columns(session, ADMIN)
    after_card = next((item for item in after_columns.ready if item.idea_id == api.id), None)
    assert after_card is not None
    assert after_card.blocked_by == []


def test_accepts_dependencies_when_creating_and_updating_a_task(session: Session) -> None:
    project, (schema,) = project_with_tasks(session, ["Schema"])

    api = ideas.create_idea(
        session, ADMIN, project_id=project.id, title="API", depends_on=[schema.id]
    )
    assert api.depends_on == [schema.id]

    cleared = ideas.update_idea(session, ADMIN, api.id, depends_on=[])
    assert cleared.depends_on == []

    untouched = ideas.update_idea(session, ADMIN, api.id, title="API v2")
    assert untouched.depends_on == []


def test_removes_a_single_dependency_edge(session: Session) -> None:
    _, (schema, docs, api) = project_with_tasks(session, ["Schema", "Docs", "API"])
    dependencies.set_dependencies(session, ADMIN, api.id, [schema.id, docs.id])

    remaining = dependencies.remove_dependency(session, ADMIN, api.id, docs.id)
    assert remaining.depends_on == [schema.id]


def test_rejects_self_dependencies_cycles_and_cross_project_blockers(session: Session) -> None:
    _, (schema, api) = project_with_tasks(session, ["Schema", "API"])
    _, other_tasks = project_with_tasks(session, ["Outside"], "OPS")

    with pytest.raises(BadRequest, match="A task cannot depend on itself"):
        dependencies.add_dependency(session, ADMIN, api.id, api.id)

    with pytest.raises(BadRequest, match="Dependencies must stay in the same project"):
        dependencies.add_dependency(session, ADMIN, api.id, other_tasks[0].id)

    dependencies.add_dependency(session, ADMIN, api.id, schema.id)
    with pytest.raises(Conflict, match="Dependency cycle detected"):
        dependencies.add_dependency(session, ADMIN, schema.id, api.id)

    unchanged = ideas.list_ideas(session, ADMIN)
    assert find(unchanged, schema.id).depends_on == []


def test_rejects_unknown_blockers(session: Session) -> None:
    _, tasks = project_with_tasks(session, ["Schema"])

    with pytest.raises(NotFound, match="Task not found: missing-task"):
        dependencies.add_dependency(session, ADMIN, tasks[0].id, "missing-task")


def test_keeps_dependency_writes_inside_the_token_project_scope(session: Session) -> None:
    project, tasks = project_with_tasks(session, ["Schema", "API"])
    _, outside_tasks = project_with_tasks(session, ["Outside"], "OPS")
    scoped = token_access("token-1", [project.id])

    with pytest.raises(NotFound, match=f"Task not found: {outside_tasks[0].id}"):
        dependencies.add_dependency(session, scoped, outside_tasks[0].id, tasks[0].id)
    with pytest.raises(NotFound, match=f"Task not found: {outside_tasks[0].id}"):
        dependencies.add_dependency(session, scoped, tasks[0].id, outside_tasks[0].id)

    linked = dependencies.add_dependency(session, scoped, tasks[1].id, tasks[0].id)
    assert linked.depends_on == [tasks[0].id]


def test_returns_a_dependency_resolved_work_order(session: Session) -> None:
    project, (schema, api, deploy) = project_with_tasks(session, ["Schema", "API", "Deploy"])
    dependencies.add_dependency(session, ADMIN, api.id, schema.id)
    dependencies.add_dependency(session, ADMIN, deploy.id, api.id)

    order = ideas.work_order(session, ADMIN, project.id)
    assert [item.id for item in order.items] == [schema.id, api.id, deploy.id]
    assert [item.task_id for item in order.items] == ["APP-1", "APP-2", "APP-3"]
    assert [item.is_actionable for item in order.items] == [True, False, False]

    finish_task(session, schema.id)
    after_first = ideas.work_order(session, ADMIN, project.id)
    assert [item.is_actionable for item in after_first.items] == [False, True, False]
    assert after_first.items[0].is_complete is True
    assert after_first.items[1].card_id is None


def test_scopes_the_work_order_to_the_projects_a_token_may_read(session: Session) -> None:
    project, tasks = project_with_tasks(session, ["Schema", "API"])
    _, outside_tasks = project_with_tasks(session, ["Outside"], "OPS")
    scoped = token_access("token-1", [project.id])

    order = ideas.work_order(session, scoped)
    assert sorted(item.id for item in order.items) == sorted([tasks[0].id, tasks[1].id])
    assert outside_tasks[0].id not in [item.id for item in order.items]


def test_exposes_dependency_tools_over_mcp(session: Session) -> None:
    """Stands in for the MCP handlers (add_idea_dependency, list_work_order,
    set_idea_dependencies, remove_idea_dependency) until they are ported: the same
    service calls with the same admin access context."""
    project, (schema, api) = project_with_tasks(session, ["Schema", "API"])

    linked = dependencies.add_dependency(session, ADMIN, api.id, schema.id)
    assert linked.depends_on == [schema.id]

    order = ideas.work_order(session, ADMIN, project.id)
    assert [item.id for item in order.items] == [schema.id, api.id]

    replaced = dependencies.set_dependencies(session, ADMIN, api.id, [])
    assert replaced.depends_on == []

    dependencies.add_dependency(session, ADMIN, api.id, schema.id)
    removed = dependencies.remove_dependency(session, ADMIN, api.id, schema.id)
    assert removed.depends_on == []
