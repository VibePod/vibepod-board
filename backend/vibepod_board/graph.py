"""Task dependency graph: cycle detection and the dependency-resolved work order.

Port of `src/shared/dependencies.ts`; the client keeps its own copy for the task graph view,
so both must apply the same rules.
"""

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from vibepod_board.enums import BoardColumn, IdeaStatus
from vibepod_board.schemas import BoardCard, Idea, TaskWorkOrder, WorkOrderItem

Edges = Mapping[str, list[str]]


def format_task_key(project_key: str, task_number: int) -> str:
    return f"{project_key}-{task_number}"


def normalize_ids(ids: Iterable[str] | None) -> list[str]:
    """Trimmed, non-empty and de-duplicated, keeping the first occurrence's position."""
    return list(dict.fromkeys(item.strip() for item in ids or [] if item.strip()))


def is_task_complete(status: str, column: str | None) -> bool:
    """A blocker stops blocking once it is done, or denied: denied work is never coming."""
    return column == BoardColumn.DONE or status == IdeaStatus.DENIED


def dependency_map(tasks: Iterable[Idea]) -> dict[str, list[str]]:
    return {task.id: normalize_ids(task.depends_on) for task in tasks}


def _order(edges: Edges) -> tuple[list[str], list[str]]:
    """Kahn's algorithm: dependencies first, whatever is left over sits in or behind a cycle."""
    dependents: dict[str, list[str]] = {}
    remaining: dict[str, int] = {}
    for task_id, dependencies in edges.items():
        known = [dependency for dependency in dependencies if dependency in edges]
        remaining[task_id] = len(known)
        for dependency in known:
            dependents.setdefault(dependency, []).append(task_id)

    ready = deque(task_id for task_id, count in remaining.items() if count == 0)
    ordered: list[str] = []
    while ready:
        task_id = ready.popleft()
        ordered.append(task_id)
        for dependent in dependents.get(task_id, []):
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                ready.append(dependent)

    ordered_ids = set(ordered)
    return ordered, [task_id for task_id in edges if task_id not in ordered_ids]


def find_cyclic_task_ids(edges: Edges) -> list[str]:
    return _order(edges)[1]


@dataclass(frozen=True)
class _Node:
    id: str
    task_id: str
    project_id: str
    project_key: str
    task_number: int
    title: str
    status: IdeaStatus
    column: BoardColumn | None
    card_id: str | None
    depends_on: list[str]


def build_work_order(
    tasks: Iterable[Idea], cards: Iterable[BoardCard], project_keys: Mapping[str, str]
) -> TaskWorkOrder:
    card_by_task = {card.idea_id: card for card in cards if card.idea_id}
    nodes: list[_Node] = []
    for task in tasks:
        card = card_by_task.get(task.id)
        project_key = project_keys.get(task.project_id, "")
        nodes.append(
            _Node(
                id=task.id,
                task_id=format_task_key(project_key, task.task_number),
                project_id=task.project_id,
                project_key=project_key,
                task_number=task.task_number,
                title=task.title,
                status=task.status,
                column=card.column if card else None,
                card_id=card.id if card else None,
                depends_on=normalize_ids(task.depends_on),
            )
        )
    return _work_order_from_nodes(nodes)


def _work_order_from_nodes(nodes: list[_Node]) -> TaskWorkOrder:
    node_by_id = {node.id: node for node in nodes}
    edges = {
        node.id: [dependency for dependency in node.depends_on if dependency in node_by_id]
        for node in nodes
    }
    ordered, cyclic = _order(edges)

    waves: dict[str, int] = {}
    for task_id in ordered:
        waves[task_id] = max([0, *(waves.get(dep, 0) + 1 for dep in edges[task_id])])

    complete = {node.id: is_task_complete(node.status, node.column) for node in nodes}
    sorted_nodes = sorted(
        (node_by_id[task_id] for task_id in ordered),
        key=lambda node: (waves[node.id], node.project_key, node.task_number),
    )

    items: list[WorkOrderItem] = []
    for position, node in enumerate(sorted_nodes, start=1):
        blocked_by = [dep for dep in edges[node.id] if not complete.get(dep, False)]
        is_complete = complete[node.id]
        items.append(
            WorkOrderItem(
                id=node.id,
                task_id=node.task_id,
                project_id=node.project_id,
                title=node.title,
                status=node.status,
                column=node.column,
                card_id=node.card_id,
                position=position,
                wave=waves[node.id],
                depends_on=node.depends_on,
                blocked_by=blocked_by,
                is_blocked=bool(blocked_by),
                is_complete=is_complete,
                is_actionable=not is_complete and not blocked_by,
            )
        )
    return TaskWorkOrder(items=items, cyclic_task_ids=cyclic)
