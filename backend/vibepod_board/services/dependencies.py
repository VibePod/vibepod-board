"""Task dependency edges: decoration of ideas/cards with `dependsOn`/`blocks`/`blockedBy`,
and validated replacement of a task's blockers."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, or_
from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, assert_can_access_project
from vibepod_board.errors import BadRequest, BoardError, Conflict, NotFound
from vibepod_board.graph import find_cyclic_task_ids, is_task_complete, normalize_ids
from vibepod_board.schemas import BoardCard, Idea, now
from vibepod_board.services.common import (
    add_activity,
    idea_from_row,
    transactional,
)
from vibepod_board.services.references import require_idea_ref, resolve_idea_id
from vibepod_board.tables import BoardCardRow, IdeaRow, TaskDependencyRow


@dataclass
class _DependencyContext:
    depends_on: dict[str, list[str]] = field(default_factory=dict)
    blocks: dict[str, list[str]] = field(default_factory=dict)
    complete: dict[str, bool] = field(default_factory=dict)

    def blocked_by(self, depends_on: list[str]) -> list[str]:
        return [task_id for task_id in depends_on if not self.complete.get(task_id, False)]


def _dependency_context(session: Session, idea_ids: Iterable[str]) -> _DependencyContext:
    """Edges touching the given tasks plus the completion state of every blocker."""
    context = _DependencyContext()
    ids = list(dict.fromkeys(idea_ids))
    if not ids:
        return context

    edges = session.exec(
        select(TaskDependencyRow)
        .where(
            or_(
                col(TaskDependencyRow.idea_id).in_(ids),
                col(TaskDependencyRow.depends_on_idea_id).in_(ids),
            )
        )
        .order_by(col(TaskDependencyRow.idea_id), col(TaskDependencyRow.depends_on_idea_id))
    ).all()
    for edge in edges:
        context.depends_on.setdefault(edge.idea_id, []).append(edge.depends_on_idea_id)
        context.blocks.setdefault(edge.depends_on_idea_id, []).append(edge.idea_id)

    blocker_ids = list(dict.fromkeys(edge.depends_on_idea_id for edge in edges))
    if blocker_ids:
        states = session.exec(
            select(IdeaRow.id, IdeaRow.status, BoardCardRow.column_name)
            .select_from(IdeaRow)
            .outerjoin(BoardCardRow, col(BoardCardRow.idea_id) == IdeaRow.id)
            .where(col(IdeaRow.id).in_(blocker_ids))
        ).all()
        for task_id, status, column in states:
            context.complete[task_id] = is_task_complete(status, column) or context.complete.get(
                task_id, False
            )
    return context


def decorate_ideas(session: Session, ideas: list[Idea]) -> list[Idea]:
    if not ideas:
        return ideas
    context = _dependency_context(session, (idea.id for idea in ideas))
    decorated = []
    for idea in ideas:
        depends_on = context.depends_on.get(idea.id, [])
        decorated.append(
            idea.model_copy(
                update={
                    "depends_on": depends_on,
                    "blocks": context.blocks.get(idea.id, []),
                    "blocked_by": context.blocked_by(depends_on),
                }
            )
        )
    return decorated


def decorate_idea(session: Session, idea: Idea | IdeaRow) -> Idea:
    if isinstance(idea, IdeaRow):
        idea = idea_from_row(idea)
    return decorate_ideas(session, [idea])[0]


def decorate_cards(session: Session, cards: list[BoardCard]) -> list[BoardCard]:
    idea_ids = [card.idea_id for card in cards if card.idea_id]
    if not idea_ids:
        return cards
    context = _dependency_context(session, idea_ids)
    decorated = []
    for card in cards:
        depends_on = context.depends_on.get(card.idea_id, []) if card.idea_id else []
        decorated.append(
            card.model_copy(
                update={"depends_on": depends_on, "blocked_by": context.blocked_by(depends_on)}
            )
        )
    return decorated


def decorate_card(session: Session, card: BoardCard) -> BoardCard:
    return decorate_cards(session, [card])[0]


def _assert_acyclic(session: Session, idea: IdeaRow, depends_on_ids: list[str]) -> None:
    rows = session.exec(
        select(TaskDependencyRow.idea_id, TaskDependencyRow.depends_on_idea_id)
        .join(IdeaRow, col(IdeaRow.id) == TaskDependencyRow.idea_id)
        .where(IdeaRow.project_id == idea.project_id, TaskDependencyRow.idea_id != idea.id)
    ).all()
    edges: dict[str, list[str]] = {}
    for task_id, depends_on_id in rows:
        edges.setdefault(task_id, []).append(depends_on_id)
    if depends_on_ids:
        edges[idea.id] = depends_on_ids

    cyclic = find_cyclic_task_ids(edges)
    if cyclic:
        raise Conflict(f"Dependency cycle detected between tasks: {', '.join(sorted(cyclic))}")


def replace_dependencies(
    session: Session,
    access: AccessContext,
    idea: IdeaRow,
    depends_on_ids: Iterable[str],
    timestamp: datetime,
) -> None:
    """Blockers must live in the same project and the graph must stay acyclic, so the work
    order can always be computed. They may be named by key, such as `VP-236`."""
    requested = normalize_ids(
        resolve_idea_id(session, access, reference) for reference in normalize_ids(depends_on_ids)
    )
    if idea.id in requested:
        raise BadRequest("A task cannot depend on itself")

    if requested:
        blockers = {
            row.id: row.project_id
            for row in session.exec(select(IdeaRow).where(col(IdeaRow.id).in_(requested)))
        }
        for task_id in requested:
            if task_id not in blockers:
                raise NotFound(f"Task not found: {task_id}")
            if blockers[task_id] != idea.project_id:
                raise BadRequest(
                    f"Dependencies must stay in the same project: {task_id} belongs to "
                    "another project"
                )

    _assert_acyclic(session, idea, requested)

    session.execute(delete(TaskDependencyRow).where(col(TaskDependencyRow.idea_id) == idea.id))
    session.add_all(
        TaskDependencyRow(idea_id=idea.id, depends_on_idea_id=task_id, created_at=timestamp)
        for task_id in requested
    )
    session.flush()


def current_dependency_ids(session: Session, idea_id: str) -> list[str]:
    return list(
        session.exec(
            select(TaskDependencyRow.depends_on_idea_id)
            .where(TaskDependencyRow.idea_id == idea_id)
            .order_by(col(TaskDependencyRow.depends_on_idea_id))
        )
    )


@transactional
def _write_dependencies(
    session: Session,
    access: AccessContext,
    idea_id: str,
    change: Callable[[list[str]], list[str]],
) -> Idea:
    idea = require_idea_ref(session, access, idea_id)
    assert_can_access_project(access, idea.project_id)
    timestamp = now()
    replace_dependencies(
        session, access, idea, change(current_dependency_ids(session, idea.id)), timestamp
    )
    # A dependency edit is a content change: age the row so a concurrent writer's
    # expectedUpdatedAt is refused rather than silently clobbering it.
    idea.updated_at = timestamp
    session.flush()
    decorated = decorate_idea(session, idea)
    add_activity(session, "idea.dependencies", f"Updated dependencies: {idea.title}", timestamp)
    return decorated


def add_dependency(
    session: Session, access: AccessContext, idea_id: str, depends_on_id: str
) -> Idea:
    return _write_dependencies(session, access, idea_id, lambda ids: [*ids, depends_on_id])


def remove_dependency(
    session: Session, access: AccessContext, idea_id: str, depends_on_id: str
) -> Idea:
    def without(ids: list[str]) -> list[str]:
        # A blocker named by key is removed too; one that no longer resolves by id is
        # matched verbatim, so a stale edge can still be dropped.
        try:
            removed = resolve_idea_id(session, access, depends_on_id)
        except BoardError:
            removed = depends_on_id.strip()
        return [i for i in ids if i != removed]

    return _write_dependencies(session, access, idea_id, without)


def set_dependencies(
    session: Session, access: AccessContext, idea_id: str, depends_on_ids: list[str]
) -> Idea:
    return _write_dependencies(session, access, idea_id, lambda _ids: list(depends_on_ids))
