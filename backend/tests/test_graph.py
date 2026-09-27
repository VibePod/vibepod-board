from vibepod_board.graph import build_work_order, find_cyclic_task_ids, is_task_complete
from vibepod_board.schemas import BoardCard, Idea

STAMP = "2026-06-04T00:00:00.000Z"
PROJECT_KEYS = {"project-1": "APP"}


def task(id: str, task_number: int, title: str, depends_on: list[str] | None = None) -> Idea:
    return Idea(
        id=id,
        project_id="project-1",
        task_number=task_number,
        title=title,
        summary="",
        details="",
        status="ready",
        labels=[],
        acceptance_criteria=[],
        depends_on=depends_on or [],
        created_at=STAMP,
        updated_at=STAMP,
    )


def card(id: str, idea_id: str, column: str) -> BoardCard:
    return BoardCard(
        id=id,
        project_id="project-1",
        idea_id=idea_id,
        title="Card",
        details="",
        column=column,
        labels=[],
        created_at=STAMP,
        updated_at=STAMP,
    )


def test_treats_done_cards_and_denied_tasks_as_complete() -> None:
    assert is_task_complete("ready", "done")
    assert is_task_complete("denied", None)
    assert not is_task_complete("ready", "in_progress")
    assert not is_task_complete("refining", None)


def test_reports_tasks_that_sit_in_or_behind_a_cycle() -> None:
    edges = {"a": ["b"], "b": ["a"], "c": ["a"], "d": []}
    assert sorted(find_cyclic_task_ids(edges)) == ["a", "b", "c"]


def test_orders_tasks_so_dependencies_come_first() -> None:
    order = build_work_order(
        [
            task("deploy", 3, "Deploy", ["api"]),
            task("api", 2, "API", ["schema"]),
            task("schema", 1, "Schema"),
            task("docs", 4, "Docs"),
        ],
        [],
        PROJECT_KEYS,
    )

    assert [item.id for item in order.items] == ["schema", "docs", "api", "deploy"]
    assert [item.position for item in order.items] == [1, 2, 3, 4]
    assert [item.wave for item in order.items] == [0, 0, 1, 2]
    assert order.items[0].task_id == "APP-1"
    assert order.cyclic_task_ids == []


def test_marks_blocked_tasks_and_clears_them_once_the_blocker_is_done() -> None:
    tasks = [task("schema", 1, "Schema"), task("api", 2, "API", ["schema"])]

    open_order = build_work_order(
        tasks, [card("card-schema", "schema", "in_progress")], PROJECT_KEYS
    )
    api = next(item for item in open_order.items if item.id == "api")
    assert api.blocked_by == ["schema"]
    assert api.is_blocked
    assert not api.is_actionable
    assert next(item for item in open_order.items if item.id == "schema").is_actionable

    finished = build_work_order(tasks, [card("card-schema", "schema", "done")], PROJECT_KEYS)
    api = next(item for item in finished.items if item.id == "api")
    assert api.blocked_by == []
    assert api.is_actionable
    assert next(item for item in finished.items if item.id == "schema").is_complete


def test_keeps_cyclic_tasks_out_of_the_order_and_reports_them() -> None:
    order = build_work_order(
        [task("a", 1, "A", ["b"]), task("b", 2, "B", ["a"]), task("c", 3, "C")],
        [],
        PROJECT_KEYS,
    )
    assert [item.id for item in order.items] == ["c"]
    assert sorted(order.cyclic_task_ids) == ["a", "b"]


def test_ignores_dependencies_on_tasks_outside_the_requested_scope() -> None:
    order = build_work_order([task("api", 2, "API", ["missing"])], [], PROJECT_KEYS)
    assert order.items[0].blocked_by == []
    assert order.items[0].depends_on == ["missing"]
    assert order.items[0].is_actionable


def test_serializes_like_the_typescript_server() -> None:
    order = build_work_order([task("api", 2, "API")], [], PROJECT_KEYS)
    assert order.model_dump(mode="json") == {
        "items": [
            {
                "id": "api",
                "taskId": "APP-2",
                "projectId": "project-1",
                "title": "API",
                "status": "ready",
                "position": 1,
                "wave": 0,
                "dependsOn": [],
                "blockedBy": [],
                "isBlocked": False,
                "isComplete": False,
                "isActionable": True,
            }
        ],
        "cyclicTaskIds": [],
    }
    assert task("x", 1, "X").model_dump(mode="json")["createdAt"] == STAMP
