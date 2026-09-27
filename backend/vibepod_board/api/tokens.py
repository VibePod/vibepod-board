from fastapi import APIRouter, status

from vibepod_board.api.models import TokenCreate, TokenUpdate
from vibepod_board.api.responses import Item, Items
from vibepod_board.auth import AdminDep
from vibepod_board.db import SessionDep
from vibepod_board.schemas import ApiTokenSummary, CreatedApiToken
from vibepod_board.services import tokens

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


@router.get("")
def list_tokens(session: SessionDep, _admin: AdminDep) -> Items[ApiTokenSummary]:
    return Items(items=tokens.list_tokens(session))


@router.post("", status_code=status.HTTP_201_CREATED)
def create_token(body: TokenCreate, session: SessionDep, _admin: AdminDep) -> CreatedApiToken:
    return tokens.create_token(session, body.name, body.project_ids)


@router.patch("/{token_id}")
def update_token(
    token_id: str, body: TokenUpdate, session: SessionDep, _admin: AdminDep
) -> Item[ApiTokenSummary]:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    return Item(item=tokens.update_token(session, token_id, **changes))


@router.post("/{token_id}/revoke")
def revoke_token(token_id: str, session: SessionDep, _admin: AdminDep) -> Item[ApiTokenSummary]:
    return Item(item=tokens.revoke_token(session, token_id))
