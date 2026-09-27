from typing import Annotated

from fastapi import APIRouter, Body, Query, Request, status

from vibepod_board.api.models import (
    DependencyAdd,
    DependencySet,
    IdeaCreate,
    IdeaUpdate,
    ReadinessRequest,
    ReadyRequest,
)
from vibepod_board.api.responses import Item, Items
from vibepod_board.auth import AccessDep
from vibepod_board.db import SessionDep
from vibepod_board.errors import NotFound
from vibepod_board.github import create_issue
from vibepod_board.schemas import BoardCard, Idea, ReadinessEvent, TaskWorkOrder
from vibepod_board.services import board, dependencies, ideas, readiness

router = APIRouter(prefix="/api", tags=["ideas"])

ProjectFilter = Annotated[str | None, Query(alias="projectId")]


def _project_filter(value: str | None) -> str | None:
    return value.strip() or None if value else None


@router.get("/ideas")
def list_ideas(
    session: SessionDep, access: AccessDep, project_id: ProjectFilter = None
) -> Items[Idea]:
    return Items(items=ideas.list_ideas(session, access, _project_filter(project_id)))


@router.post("/ideas", status_code=status.HTTP_201_CREATED)
def create_idea(body: IdeaCreate, session: SessionDep, access: AccessDep) -> Item[Idea]:
    return Item(item=ideas.create_idea(session, access, **body.model_dump(by_alias=False)))


@router.patch("/ideas/{idea_id}")
def update_idea(
    idea_id: str, body: IdeaUpdate, session: SessionDep, access: AccessDep
) -> Item[Idea]:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    return Item(item=ideas.update_idea(session, access, idea_id, **changes))


@router.post("/ideas/{idea_id}/ready")
def set_ready(
    idea_id: str,
    session: SessionDep,
    access: AccessDep,
    body: Annotated[ReadyRequest | None, Body()] = None,
) -> Item[Idea]:
    available = body.available if body else True
    return Item(item=ideas.set_board_availability(session, access, idea_id, available))


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
    return ideas.work_order(session, access, _project_filter(project_id))


@router.post("/ideas/{idea_id}/sync-github")
def sync_github(
    idea_id: str, request: Request, session: SessionDep, access: AccessDep
) -> dict[str, object]:
    settings = request.app.state.settings
    link = board.LOCAL
    if settings.github_token and settings.github_repository:
        idea = next((i for i in ideas.list_ideas(session, access) if i.id == idea_id), None)
        if idea is None:
            raise NotFound(f"Idea not found: {idea_id}")
        link = create_issue(settings, idea)
    card: BoardCard = board.create_card_from_idea(session, access, idea_id, link)
    return {"mode": link.mode, "card": card.model_dump(mode="json")}


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
