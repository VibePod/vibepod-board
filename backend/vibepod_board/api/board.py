from typing import Annotated

from fastapi import APIRouter, Query

from vibepod_board.api.models import (
    ArchiveDoneRequest,
    BoardCardBatch,
    BoardCardUpdate,
    ReadinessRequest,
)
from vibepod_board.api.queries import (
    Assignee,
    ProjectFilter,
    Unassigned,
    UpdatedSince,
    ViewParam,
    enum_list,
    project_filter,
    split_list,
)
from vibepod_board.api.responses import Columns, Item, Items
from vibepod_board.auth import AccessDep
from vibepod_board.db import SessionDep
from vibepod_board.enums import BoardColumn
from vibepod_board.schemas import BoardCard, CompactBoardCard, EntityRef, KeyedBoardCard
from vibepod_board.services import board, readiness
from vibepod_board.services.views import View, project_cards

router = APIRouter(prefix="/api/board", tags=["board"])

ProjectedCard = KeyedBoardCard | CompactBoardCard | EntityRef


@router.get("")
def get_board(
    session: SessionDep,
    access: AccessDep,
    project_id: ProjectFilter = None,
    column: Annotated[list[str] | None, Query()] = None,
    updated_since: UpdatedSince = None,
    assignee: Assignee = None,
    unassigned: Unassigned = False,
) -> Columns:
    filters = board.CardFilter(
        project=project_filter(project_id),
        column=enum_list(BoardColumn, column, "column"),
        updated_since=updated_since,
        assignee=split_list(assignee),
        unassigned=unassigned,
    )
    return Columns(columns=board.board_columns(session, access, filters=filters))


@router.get("/archived")
def list_archived(
    session: SessionDep, access: AccessDep, project_id: ProjectFilter = None
) -> Items[BoardCard]:
    return Items(items=board.list_archived_cards(session, access, project_filter(project_id)))


@router.post("/archive-done")
def archive_done(
    body: ArchiveDoneRequest, session: SessionDep, access: AccessDep
) -> Items[BoardCard]:
    return Items(items=board.archive_done_cards(session, access, body.project_id))


@router.post("/batch")
def update_cards(
    body: BoardCardBatch, session: SessionDep, access: AccessDep, view: ViewParam = View.FULL
) -> Items[ProjectedCard]:
    items = [item.model_dump(exclude_unset=True, by_alias=False) for item in body.items]
    return Items(items=project_cards(session, board.update_cards(session, access, items), view))


@router.get("/{card_id}")
def get_card(
    card_id: str, session: SessionDep, access: AccessDep, view: ViewParam = View.FULL
) -> Item[ProjectedCard]:
    """A card by its id or by any reference to its task, such as `VP-236`."""
    card = board.get_card(session, access, card_id)
    return Item(item=project_cards(session, [card], view)[0])


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
