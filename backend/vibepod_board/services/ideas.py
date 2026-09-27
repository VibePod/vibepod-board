"""Ideas (tasks): refinement, readiness for the board, and the dependency-resolved work order."""

from collections.abc import Sequence

from sqlalchemy import delete, func
from sqlmodel import Session, col, select

from vibepod_board.access import (
    AccessContext,
    AdminAccess,
    assert_can_access_project,
    default_project_id_for_create,
)
from vibepod_board.enums import IdeaStatus
from vibepod_board.graph import build_work_order
from vibepod_board.schemas import Idea, TaskWorkOrder, now
from vibepod_board.services.board import ensure_card, list_cards, sync_card_from_idea
from vibepod_board.services.common import (
    add_activity,
    assert_title,
    idea_from_row,
    new_id,
    normalize_list,
    normalize_optional_text,
    require_idea,
    require_project,
    transactional,
)
from vibepod_board.services.dependencies import decorate_idea, decorate_ideas, replace_dependencies
from vibepod_board.services.projects import list_projects, resolve_project_id_for_create
from vibepod_board.tables import BoardCardRow, IdeaRow


def list_ideas(
    session: Session, access: AccessContext, project_id: str | None = None
) -> list[Idea]:
    query = select(IdeaRow)
    if project_id:
        project = require_project(session, project_id)
        assert_can_access_project(access, project.id)
        query = query.where(IdeaRow.project_id == project.id)
    elif not isinstance(access, AdminAccess):
        if not access.project_ids:
            return []
        query = query.where(col(IdeaRow.project_id).in_(access.project_ids))
    rows = session.exec(query.order_by(col(IdeaRow.updated_at).desc(), col(IdeaRow.title))).all()
    return decorate_ideas(session, [idea_from_row(row) for row in rows])


def _next_task_number(session: Session, project_id: str) -> int:
    highest = session.exec(
        select(func.coalesce(func.max(IdeaRow.task_number), 0)).where(
            IdeaRow.project_id == project_id
        )
    ).one()
    return int(highest) + 1


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
) -> Idea:
    assert_title(title, "Idea")
    resolved_project_id = resolve_project_id_for_create(
        session, default_project_id_for_create(access, project_id)
    )
    assert_can_access_project(access, resolved_project_id)
    timestamp = now()
    idea = IdeaRow(
        id=new_id(),
        project_id=resolved_project_id,
        task_number=_next_task_number(session, resolved_project_id),
        title=title.strip(),
        summary=(summary or "").strip(),
        details=(details or "").strip(),
        status=IdeaStatus.IDEA,
        labels=normalize_list(labels),
        acceptance_criteria=normalize_list(acceptance_criteria),
        repository_local_path=normalize_optional_text(repository_local_path),
        repository_remote_url=normalize_optional_text(repository_remote_url),
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(idea)
    session.flush()
    if depends_on:
        replace_dependencies(session, idea, depends_on, timestamp)
    add_activity(session, "idea.created", f"Created idea: {idea.title}", timestamp)
    return decorate_idea(session, idea)


@transactional
def update_idea(
    session: Session,
    access: AccessContext,
    idea_id: str,
    title: str | None = None,
    summary: str | None = None,
    details: str | None = None,
    labels: Sequence[str] | None = None,
    acceptance_criteria: Sequence[str] | None = None,
    depends_on: Sequence[str] | None = None,
    status: IdeaStatus | None = None,
    repository_local_path: str | None = None,
    repository_remote_url: str | None = None,
) -> Idea:
    idea = require_idea(session, idea_id)
    assert_can_access_project(access, idea.project_id)
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

    next_status = IdeaStatus(status or idea.status)
    # Writing details or acceptance criteria is refinement, so a bare idea moves along.
    refined = details is not None or acceptance_criteria is not None
    if next_status == IdeaStatus.IDEA and refined and (idea.details or idea.acceptance_criteria):
        next_status = IdeaStatus.REFINING
    idea.status = next_status

    timestamp = now()
    idea.updated_at = timestamp
    session.flush()
    if depends_on is not None:
        replace_dependencies(session, idea, depends_on, timestamp)
    sync_card_from_idea(session, idea, timestamp)
    add_activity(session, "idea.updated", f"Updated idea: {idea.title}", timestamp)
    return decorate_idea(session, idea)


@transactional
def set_board_availability(
    session: Session, access: AccessContext, idea_id: str, available: bool
) -> Idea:
    """Ready ideas get a board card; taking an idea off the board deletes its card."""
    idea = require_idea(session, idea_id)
    assert_can_access_project(access, idea.project_id)
    timestamp = now()

    if available:
        idea.status = IdeaStatus.READY
        idea.updated_at = timestamp
        session.flush()
        ensure_card(session, idea, timestamp=timestamp)
        add_activity(session, "idea.ready", f"Marked idea ready: {idea.title}", timestamp)
        return decorate_idea(session, idea)

    session.execute(delete(BoardCardRow).where(col(BoardCardRow.idea_id) == idea.id))
    refined = bool(idea.details or idea.acceptance_criteria)
    idea.status = IdeaStatus.REFINING if refined else IdeaStatus.IDEA
    idea.updated_at = timestamp
    session.flush()
    add_activity(session, "idea.not_ready", f"Removed idea from board: {idea.title}", timestamp)
    return decorate_idea(session, idea)


def mark_ready(session: Session, access: AccessContext, idea_id: str) -> Idea:
    return set_board_availability(session, access, idea_id, True)


def work_order(
    session: Session, access: AccessContext, project_id: str | None = None
) -> TaskWorkOrder:
    tasks = list_ideas(session, access, project_id)
    cards = list_cards(session, access, project_id)
    keys = {project.id: project.key for project in list_projects(session, access)}
    return build_work_order(tasks, cards, keys)
