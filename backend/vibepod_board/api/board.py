from typing import Annotated

from fastapi import APIRouter, Query

from vibepod_board.api.models import ArchiveDoneRequest, BoardCardUpdate, ReadinessRequest
from vibepod_board.api.responses import Columns, Item, Items
from vibepod_board.auth import AccessDep
from vibepod_board.db import SessionDep
from vibepod_board.schemas import BoardCard
from vibepod_board.services import board, readiness

router = APIRouter(prefix="/api/board", tags=["board"])


@router.get("")
def get_board(
    session: SessionDep,
    access: AccessDep,
    project_id: Annotated[str | None, Query(alias="projectId")] = None,
) -> Columns:
    project_id = project_id.strip() or None if project_id else None
    return Columns(columns=board.board_columns(session, access, project_id))


@router.get("/archived")
def list_archived(
    session: SessionDep,
    access: AccessDep,
    project_id: Annotated[str | None, Query(alias="projectId")] = None,
) -> Items[BoardCard]:
    project_id = project_id.strip() or None if project_id else None
    return Items(items=board.list_archived_cards(session, access, project_id))


@router.post("/archive-done")
def archive_done(
    body: ArchiveDoneRequest, session: SessionDep, access: AccessDep
) -> Items[BoardCard]:
    return Items(items=board.archive_done_cards(session, access, body.project_id))


@router.post("/{card_id}/archive")
def archive_card(card_id: str, session: SessionDep, access: AccessDep) -> Item[BoardCard]:
    return Item(item=board.archive_card(session, access, card_id))


@router.post("/{card_id}/unarchive")
def unarchive_card(card_id: str, session: SessionDep, access: AccessDep) -> Item[BoardCard]:
    return Item(item=board.unarchive_card(session, access, card_id))


@router.patch("/{card_id}")
def update_card(
    card_id: str, body: BoardCardUpdate, session: SessionDep, access: AccessDep
) -> Item[BoardCard]:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    return Item(item=board.update_card(session, access, card_id, **changes))


@router.post("/{card_id}/readiness")
def set_card_readiness(
    card_id: str, body: ReadinessRequest, session: SessionDep, access: AccessDep
) -> Item[BoardCard]:
    return Item(
        item=readiness.set_card_readiness(session, access, card_id, body.score, body.reason)
    )
