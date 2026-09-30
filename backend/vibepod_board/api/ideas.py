from typing import Annotated

from fastapi import APIRouter, Body, Query, status

from vibepod_board.api.models import (
    DependencyAdd,
    DependencySet,
    IdeaBatch,
    IdeaCreate,
    IdeaUpdate,
    ReadinessRequest,
    ReadyRequest,
    RunReportRequest,
)
from vibepod_board.api.queries import (
    Assignee,
    Cursor,
    Limit,
    ProjectFilter,
    Unassigned,
    UpdatedSince,
    ViewParam,
    enum_list,
    project_filter,
    split_list,
)
from vibepod_board.api.responses import Item, Items, ItemsPage
from vibepod_board.auth import AccessDep
from vibepod_board.db import SessionDep
from vibepod_board.enums import IdeaStatus
from vibepod_board.schemas import (
    CompactIdea,
    DeletedIdea,
    EntityRef,
    Idea,
    KeyedIdea,
    ReadinessEvent,
    TaskEvent,
    TaskRun,
    TaskWorkOrder,
)
from vibepod_board.services import board, dependencies, history, ideas, readiness, runs
from vibepod_board.services.views import View, project_ideas

router = APIRouter(prefix="/api", tags=["ideas"])

ProjectedIdea = KeyedIdea | CompactIdea | EntityRef


@router.get("/ideas")
def list_ideas(
    session: SessionDep,
    access: AccessDep,
    project_id: ProjectFilter = None,
    status: Annotated[list[str] | None, Query()] = None,
    updated_since: UpdatedSince = None,
    assignee: Assignee = None,
    unassigned: Unassigned = False,
    limit: Limit = None,
    cursor: Cursor = None,
    view: ViewParam = View.FULL,
) -> ItemsPage[ProjectedIdea]:
    filters = ideas.IdeaFilter(
        project=project_filter(project_id),
        status=enum_list(IdeaStatus, status, "status"),
        updated_since=updated_since,
        assignee=split_list(assignee),
        unassigned=unassigned,
        limit=limit,
        cursor=cursor,
    )
    page = ideas.list_ideas_page(session, access, filters)
    # A compact task reports its board column.
    cards = (
        board.list_cards(session, access, filters=board.CardFilter(project=filters.project))
        if view == View.COMPACT
        else None
    )
    return ItemsPage(
        items=project_ideas(session, page.items, view, cards), next_cursor=page.next_cursor
    )


@router.post("/ideas", status_code=status.HTTP_201_CREATED)
def create_idea(body: IdeaCreate, session: SessionDep, access: AccessDep) -> Item[Idea]:
    return Item(item=ideas.create_idea(session, access, **body.model_dump(by_alias=False)))


@router.post("/ideas/batch")
def update_ideas(
    body: IdeaBatch, session: SessionDep, access: AccessDep, view: ViewParam = View.FULL
) -> Items[ProjectedIdea]:
    items = [item.model_dump(exclude_unset=True, by_alias=False) for item in body.items]
    return Items(items=project_ideas(session, ideas.update_ideas(session, access, items), view))


@router.get("/ideas/{idea_id}")
def get_idea(
    idea_id: str, session: SessionDep, access: AccessDep, view: ViewParam = View.FULL
) -> Item[ProjectedIdea]:
    """A task by id, key such as `VP-236`, or bare number when one project is in scope."""
    idea = ideas.get_idea(session, access, idea_id)
    return Item(item=project_ideas(session, [idea], view)[0])


@router.patch("/ideas/{idea_id}")
def update_idea(
    idea_id: str, body: IdeaUpdate, session: SessionDep, access: AccessDep
) -> Item[Idea]:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    return Item(item=ideas.update_idea(session, access, idea_id, **changes))


@router.delete("/ideas/{idea_id}")
def delete_idea(idea_id: str, session: SessionDep, access: AccessDep) -> DeletedIdea:
    return ideas.delete_idea(session, access, idea_id)


@router.post("/ideas/{idea_id}/ready")
def set_ready(
    idea_id: str,
    session: SessionDep,
    access: AccessDep,
    body: Annotated[ReadyRequest | None, Body()] = None,
) -> Item[Idea]:
    body = body or ReadyRequest()
    return Item(
        item=ideas.set_board_availability(session, access, idea_id, body.available, body.readiness)
    )


@router.put("/ideas/{idea_id}/dependencies")
def set_dependencies(
    idea_id: str, body: DependencySet, session: SessionDep, access: AccessDep
) -> Item[Idea]:
    return Item(item=dependencies.set_dependencies(session, access, idea_id, body.depends_on_ids))


@router.post("/ideas/{idea_id}/dependencies", status_code=status.HTTP_201_CREATED)
def add_dependency(
    idea_id: str, body: DependencyAdd, session: SessionDep, access: AccessDep
) -> Item[Idea]:
    return Item(item=dependencies.add_dependency(session, access, idea_id, body.depends_on_id))


@router.delete("/ideas/{idea_id}/dependencies/{depends_on_id}")
def remove_dependency(
    idea_id: str, depends_on_id: str, session: SessionDep, access: AccessDep
) -> Item[Idea]:
    return Item(item=dependencies.remove_dependency(session, access, idea_id, depends_on_id))


@router.get("/work-order")
def work_order(
    session: SessionDep, access: AccessDep, project_id: ProjectFilter = None
) -> TaskWorkOrder:
    return ideas.work_order(session, access, project_filter(project_id))


@router.post("/ideas/{idea_id}/readiness")
def set_idea_readiness(
    idea_id: str, body: ReadinessRequest, session: SessionDep, access: AccessDep
) -> Item[Idea]:
    return Item(
        item=readiness.set_idea_readiness(session, access, idea_id, body.score, body.reason)
    )


@router.get("/ideas/{idea_id}/readiness")
def list_idea_readiness(
    idea_id: str, session: SessionDep, access: AccessDep
) -> Items[ReadinessEvent]:
    return Items(items=readiness.list_idea_readiness(session, access, idea_id))


@router.get("/ideas/{idea_id}/history")
def task_history(idea_id: str, session: SessionDep, access: AccessDep) -> Items[TaskEvent]:
    """What happened to the task under automation, newest first."""
    return Items(items=history.list_task_history(session, access, idea_id))


@router.post("/ideas/{idea_id}/runs", status_code=status.HTTP_201_CREATED)
def add_run_report(
    idea_id: str, body: RunReportRequest, session: SessionDep, access: AccessDep
) -> Item[TaskRun]:
    """Adds the report of an automated run to the task. Long verify output is cut down to
    its head and tail."""
    return Item(
        item=runs.add_run_report(session, access, idea_id, **body.model_dump(by_alias=False))
    )


@router.get("/ideas/{idea_id}/runs")
def list_run_reports(
    idea_id: str,
    session: SessionDep,
    access: AccessDep,
    limit: Annotated[int | None, Query(ge=1, le=runs.RUNS_PAGE_MAX)] = None,
    before: Annotated[str | None, Query(description="nextCursor of the previous page.")] = None,
) -> ItemsPage[TaskRun]:
    """The task's automated runs, newest first, a page at a time (20 by default). Pass the
    page's nextCursor back as `before` for older runs."""
    page = runs.list_run_reports(session, access, idea_id, limit=limit, before=before)
    return ItemsPage(items=page.items, next_cursor=page.next_cursor)


@router.get("/readiness")
def list_readiness(
    session: SessionDep,
    access: AccessDep,
    project_id: ProjectFilter = None,
    tasks: Annotated[list[str] | None, Query(description="Task ids or keys.")] = None,
    latest_only: Annotated[bool, Query(alias="latestOnly")] = True,
) -> Items[ReadinessEvent]:
    """The latest score per task for a project or for named tasks; `latestOnly=false`
    returns the full history."""
    return Items(
        items=readiness.list_readiness(
            session, access, project_filter(project_id), split_list(tasks), latest_only
        )
    )
