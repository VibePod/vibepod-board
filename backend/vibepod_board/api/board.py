from typing import Annotated

from fastapi import APIRouter, Query

from vibepod_board.api.models import BoardCardUpdate, ReadinessRequest
from vibepod_board.api.responses import Columns, Item
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
