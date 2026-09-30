"""Ideas (tasks): refinement, readiness for the board, and the dependency-resolved work order."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func
from sqlmodel import Session, col, select

from vibepod_board.access import (
    AccessContext,
    assert_can_access_project,
    default_project_id_for_create,
)
from vibepod_board.enums import BoardColumn, IdeaStatus, TaskEventKind
from vibepod_board.errors import BoardError, Conflict
from vibepod_board.graph import build_work_order, format_task_key
from vibepod_board.schemas import DeletedIdea, Idea, TaskWorkOrder, now
from vibepod_board.services.board import (
    CardFilter,
    batch_error,
    check_batch_size,
    ensure_card,
    list_cards,
    sync_card_from_idea,
)
from vibepod_board.services.common import (
    add_activity,
    assert_title,
    assert_unchanged,
    idea_from_row,
    lock,
    new_id,
    normalize_list,
    normalize_optional_text,
    require_project,
    transactional,
)
from vibepod_board.services.dependencies import decorate_idea, decorate_ideas, replace_dependencies
from vibepod_board.services.github_link import link_url
from vibepod_board.services.history import actor_for, add_task_event
from vibepod_board.services.listing import (
    ListFilter,
    Page,
    list_scope,
    page_query,
    scoped,
    with_cursor,
)
from vibepod_board.services.open_reviews import end_open_reviews
from vibepod_board.services.projects import list_projects, resolve_project_id_for_create
from vibepod_board.services.readiness import apply_idea_readiness
from vibepod_board.services.references import (
    require_idea_ref,
    resolve_project_id,
)
from vibepod_board.tables import (
    BoardCardRow,
    DocumentRow,
    IdeaRow,
    ProjectRow,
    TaskDependencyRow,
)


@dataclass(frozen=True)
class IdeaFilter(ListFilter):
    status: Sequence[IdeaStatus] = ()


def list_ideas_page(session: Session, access: AccessContext, filters: IdeaFilter) -> Page[Idea]:
    scope = list_scope(session, access, filters.project)
    if scope == []:
        return Page([])
    query = scoped(select(IdeaRow), IdeaRow.project_id, scope)
    if filters.status:
        query = query.where(col(IdeaRow.status).in_(list(filters.status)))
    rows = list(session.exec(page_query(query, IdeaRow, filters)).all())
    items = decorate_ideas(session, [idea_from_row(row) for row in rows])
    return with_cursor(rows, items, filters.limit)


def list_ideas(
    session: Session,
    access: AccessContext,
    project_id: str | None = None,
    filters: IdeaFilter | None = None,
) -> list[Idea]:
    return list_ideas_page(session, access, filters or IdeaFilter(project=project_id)).items


def get_idea(session: Session, access: AccessContext, reference: str) -> Idea:
    """One task by id, key such as `VP-236`, or bare number when one project is in scope."""
    return decorate_idea(session, require_idea_ref(session, access, reference))


def _project_for_create(session: Session, access: AccessContext, reference: str | None) -> str:
    requested = resolve_project_id(session, reference) if reference else None
    project_id = resolve_project_id_for_create(
        session, default_project_id_for_create(access, requested)
    )
    assert_can_access_project(access, project_id)
    return project_id


def next_task_number(session: Session, project_id: str) -> int:
    """Issues the project's next task number. The project row is locked, so concurrent
    creates wait for each other, and the high-water mark keeps deleted numbers retired."""
    project = session.exec(
        select(ProjectRow).where(ProjectRow.id == project_id).with_for_update()
    ).one()
    highest = session.exec(
        select(func.coalesce(func.max(IdeaRow.task_number), 0)).where(
            IdeaRow.project_id == project_id
        )
    ).one()
    project.last_task_number = max(project.last_task_number, int(highest)) + 1
    return project.last_task_number


@transactional
def create_idea(
    session: Session,
    access: AccessContext,
    title: str,
    project_id: str | None = None,
    summary: str | None = None,
    details: str | None = None,
    labels: Sequence[str] | None = None,
    acceptance_criteria: Sequence[str] | None = None,
    depends_on: Sequence[str] | None = None,
    repository_local_path: str | None = None,
    repository_remote_url: str | None = None,
    github_issue_url: str | None = None,
    assignee: str | None = None,
) -> Idea:
    assert_title(title, "Idea")
    resolved_project_id = _project_for_create(session, access, project_id)
    timestamp = now()
    idea = IdeaRow(
        id=new_id(),
        project_id=resolved_project_id,
        task_number=next_task_number(session, resolved_project_id),
        title=title.strip(),
        summary=(summary or "").strip(),
        details=(details or "").strip(),
        status=IdeaStatus.IDEA,
        labels=normalize_list(labels),
        acceptance_criteria=normalize_list(acceptance_criteria),
        repository_local_path=normalize_optional_text(repository_local_path),
        repository_remote_url=normalize_optional_text(repository_remote_url),
        assignee=normalize_optional_text(assignee),
        created_at=timestamp,
        updated_at=timestamp,
    )
    if github_issue_url:
        link_url(session, idea, github_issue_url)
    session.add(idea)
    session.flush()
    if depends_on:
        replace_dependencies(session, access, idea, depends_on, timestamp)
    add_activity(session, "idea.created", f"Created idea: {idea.title}", timestamp)
    return decorate_idea(session, idea)


def _label(session: Session, idea: IdeaRow) -> str:
    project = session.get(ProjectRow, idea.project_id)
    return f"Task {format_task_key(project.key, idea.task_number)}" if project else "Task"


def _take_off_board(
    session: Session, idea: IdeaRow, timestamp: datetime, actor: str | None = None
) -> None:
    archived = session.exec(
        select(BoardCardRow.id).where(
            BoardCardRow.idea_id == idea.id, col(BoardCardRow.archived_at).is_not(None)
        )
    ).first()
    if archived:
        raise Conflict("Task is archived; unarchive its card before taking it off the board")
    claimed = session.exec(
        select(BoardCardRow).where(
            BoardCardRow.idea_id == idea.id, col(BoardCardRow.claimed_at).is_not(None)
        )
    ).first()
    if claimed is not None and claimed.assignee == idea.assignee:
        # The claim goes with the card; its holder must not stay on the task, or the task
        # would come back to the board held by a runner that no longer works on it.
        idea.assignee = None
        add_task_event(
            session,
            idea.id,
            TaskEventKind.CLAIM_ENDED,
            f"Claim by {claimed.assignee} ended: taken off the board",
            actor,
            timestamp,
        )
    session.execute(delete(BoardCardRow).where(col(BoardCardRow.idea_id) == idea.id))


def _end_claim_on_denial(
    session: Session, idea: IdeaRow, timestamp: datetime, actor: str | None
) -> None:
    """Denying a task ends a claim on it and its open reviews: no runner or reviewer must keep
    working on, or holding, a task nobody wants done. A claimed card goes back to Planned,
    where denied tasks are skipped."""
    end_open_reviews(session, idea.id, timestamp, "the task was denied", actor)
    claimed = session.exec(
        select(BoardCardRow.id).where(
            BoardCardRow.idea_id == idea.id, col(BoardCardRow.claimed_at).is_not(None)
        )
    ).first()
    if claimed is None:
        return
    card = lock(session, BoardCardRow, claimed)
    if card.claimed_at is None:
        return
    holder = card.assignee
    card.claimed_at = None
    card.claim_expires_at = None
    card.assignee = None
    card.column_name = BoardColumn.PLANNED
    card.updated_at = timestamp
    if idea.assignee == holder:
        idea.assignee = None
    add_task_event(
        session,
        idea.id,
        TaskEventKind.CLAIM_ENDED,
        f"Claim by {holder} ended: the task was denied",
        actor,
        timestamp,
    )


def _apply_idea_update(
    session: Session,
    access: AccessContext,
    reference: str,
    timestamp: datetime,
    title: str | None = None,
    summary: str | None = None,
    details: str | None = None,
    labels: Sequence[str] | None = None,
    acceptance_criteria: Sequence[str] | None = None,
    depends_on: Sequence[str] | None = None,
    status: IdeaStatus | None = None,
    repository_local_path: str | None = None,
    repository_remote_url: str | None = None,
    github_issue_url: str | None = None,
    assignee: str | None = None,
    on_board: bool | None = None,
    readiness: Any = None,
    expected_updated_at: Any = None,
) -> tuple[IdeaRow, bool]:
    """One task write without its own transaction or activity row, so a batch can apply
    many of them atomically. Also says whether the task joined the board."""
    resolved = require_idea_ref(session, access, reference)
    assert_can_access_project(access, resolved.project_id)
    idea = lock(session, IdeaRow, resolved.id)
    assert_unchanged(_label(session, idea), expected_updated_at, idea.updated_at)
    previous_status = idea.status
    if title is not None:
        assert_title(title, "Idea")
        idea.title = title.strip()
    if summary is not None:
        idea.summary = summary.strip()
    if details is not None:
        idea.details = details.strip()
    if labels is not None:
        idea.labels = normalize_list(labels)
    if acceptance_criteria is not None:
        idea.acceptance_criteria = normalize_list(acceptance_criteria)
    if repository_local_path is not None:
        idea.repository_local_path = normalize_optional_text(repository_local_path)
    if repository_remote_url is not None:
        idea.repository_remote_url = normalize_optional_text(repository_remote_url)
    if assignee is not None:
        idea.assignee = normalize_optional_text(assignee)
    if github_issue_url is not None:
        link_url(session, idea, github_issue_url)

    next_status = IdeaStatus(status or idea.status)
    # Writing details or acceptance criteria is refinement, so a bare idea moves along.
    refined = details is not None or acceptance_criteria is not None
    if next_status == IdeaStatus.IDEA and refined and (idea.details or idea.acceptance_criteria):
        next_status = IdeaStatus.REFINING
    idea.status = next_status
    if next_status == IdeaStatus.DENIED:
        _end_claim_on_denial(session, idea, timestamp, actor_for(session, access))

    idea.updated_at = timestamp
    session.flush()
    if depends_on is not None:
        replace_dependencies(session, access, idea, depends_on, timestamp)
    sync_card_from_idea(
        session, idea, timestamp, actor_for(session, access), holder_changed=assignee is not None
    )

    # Status says what the task is; on_board says whether it has a card. Reaching "ready"
    # ensures one, but leaving "ready" never destroys one: that loses the card's id, column
    # and branch, so it needs on_board=False.
    became_ready = next_status == IdeaStatus.READY and previous_status != IdeaStatus.READY
    joins_board = on_board is True or became_ready
    if joins_board:
        ensure_card(session, idea, timestamp)
    elif on_board is False:
        _take_off_board(session, idea, timestamp, actor_for(session, access))

    if readiness is not None:
        # One timestamp for content and score, so the score reads as "evaluated as of this
        # edit" rather than instantly stale.
        score, reason = _readiness_fields(readiness)
        apply_idea_readiness(session, idea, score, reason, timestamp)
    return idea, joins_board


def _readiness_fields(readiness: Any) -> tuple[Any, str | None]:
    if isinstance(readiness, dict):
        return readiness.get("score"), readiness.get("reason")
    return readiness.score, readiness.reason


@transactional
def update_idea(session: Session, access: AccessContext, idea_id: str, **changes: Any) -> Idea:
    timestamp = now()
    idea, joins_board = _apply_idea_update(session, access, idea_id, timestamp, **changes)
    if joins_board:
        add_activity(session, "idea.ready", f"Marked idea ready: {idea.title}", timestamp)
    else:
        add_activity(session, "idea.updated", f"Updated idea: {idea.title}", timestamp)
    return decorate_idea(session, idea)


@transactional
def update_ideas(
    session: Session, access: AccessContext, items: Sequence[dict[str, Any]]
) -> list[Idea]:
    """Applies many task writes in one transaction. All-or-nothing: agents run several
    sessions against one board, and a half-applied batch is worse than a refused one."""
    check_batch_size(items)
    if not items:
        return []
    timestamp = now()
    written: list[IdeaRow] = []
    for index, item in enumerate(items):
        changes = dict(item)
        reference = changes.pop("id")
        try:
            idea, _joins_board = _apply_idea_update(
                session, access, reference, timestamp, **changes
            )
        except BoardError as error:
            raise batch_error(index, reference, error) from error
        written.append(idea)
    # One activity row, because every insert also prunes the activity table.
    add_activity(session, "idea.updated", f"Updated {len(written)} tasks", timestamp)
    return decorate_ideas(session, [idea_from_row(idea) for idea in written])


@transactional
def set_board_availability(
    session: Session,
    access: AccessContext,
    idea_id: str,
    available: bool,
    readiness: Any = None,
) -> Idea:
    """Ready ideas get a board card; taking an idea off the board deletes its card. A
    readiness score given along is recorded in the same write."""
    idea = require_idea_ref(session, access, idea_id)
    assert_can_access_project(access, idea.project_id)
    idea = lock(session, IdeaRow, idea.id)
    timestamp = now()

    if available:
        idea.status = IdeaStatus.READY
        idea.updated_at = timestamp
        session.flush()
        if readiness is not None:
            score, reason = _readiness_fields(readiness)
            apply_idea_readiness(session, idea, score, reason, timestamp)
        ensure_card(session, idea, timestamp)
        add_activity(session, "idea.ready", f"Marked idea ready: {idea.title}", timestamp)
        return decorate_idea(session, idea)

    _take_off_board(session, idea, timestamp, actor_for(session, access))
    refined = bool(idea.details or idea.acceptance_criteria)
    idea.status = IdeaStatus.REFINING if refined else IdeaStatus.IDEA
    idea.updated_at = timestamp
    session.flush()
    add_activity(session, "idea.not_ready", f"Removed idea from board: {idea.title}", timestamp)
    return decorate_idea(session, idea)


def mark_ready(
    session: Session, access: AccessContext, idea_id: str, readiness: Any = None
) -> Idea:
    return set_board_availability(session, access, idea_id, True, readiness)


def work_order(
    session: Session, access: AccessContext, project_id: str | None = None
) -> TaskWorkOrder:
    tasks = list_ideas(session, access, project_id)
    cards = list_cards(session, access, filters=CardFilter(project=project_id))
    keys = {project.id: project.key for project in list_projects(session, access)}
    return build_work_order(tasks, cards, keys)


@transactional
def delete_idea(session: Session, access: AccessContext, idea_id: str) -> DeletedIdea:
    """Deletes a task for good: its board card, dependency edges and readiness history go
    with it, and documents stop linking to it. A linked GitHub issue is left alone."""
    idea = require_idea_ref(session, access, idea_id)
    assert_can_access_project(access, idea.project_id)
    # Task before card, the order every write takes.
    idea = lock(session, IdeaRow, idea.id)
    project = require_project(session, idea.project_id)
    task_id = format_task_key(project.key, idea.task_number)
    dependents = list(
        session.exec(
            select(TaskDependencyRow.idea_id)
            .where(TaskDependencyRow.depends_on_idea_id == idea.id)
            .order_by(col(TaskDependencyRow.idea_id))
        )
    )

    card_ids = set(session.exec(select(BoardCardRow.id).where(BoardCardRow.idea_id == idea.id)))
    documents = session.exec(
        select(DocumentRow).where(DocumentRow.project_id == idea.project_id)
    ).all()
    for document in documents:
        if idea.id in document.linked_idea_ids:
            document.linked_idea_ids = [i for i in document.linked_idea_ids if i != idea.id]
        if card_ids & set(document.linked_card_ids):
            document.linked_card_ids = [i for i in document.linked_card_ids if i not in card_ids]

    session.execute(delete(BoardCardRow).where(col(BoardCardRow.idea_id) == idea.id))
    title = idea.title
    deleted_id = idea.id
    session.delete(idea)
    session.flush()
    add_activity(session, "idea.deleted", f"Deleted task {task_id}: {title}")
    return DeletedIdea(id=deleted_id, task_id=task_id, dependents=dependents)
