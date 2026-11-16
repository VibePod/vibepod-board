"""API tokens. The admin manages every token; a user manages their own, for their own
projects."""

from fastapi import APIRouter, status

from vibepod_board.api.models import TokenCreate, TokenUpdate
from vibepod_board.api.responses import Item, Items
from vibepod_board.auth import MemberDep, acting_user_id
from vibepod_board.db import SessionDep
from vibepod_board.schemas import ApiTokenSummary, CreatedApiToken
from vibepod_board.services import tokens

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


@router.get("")
def list_tokens(session: SessionDep, member: MemberDep) -> Items[ApiTokenSummary]:
    """The admin sees every token, with `owner` naming the user a token belongs to; a user
    sees their own."""
    return Items(items=tokens.list_tokens(session, acting_user_id(member)))


@router.post("", status_code=status.HTTP_201_CREATED)
def create_token(body: TokenCreate, session: SessionDep, member: MemberDep) -> CreatedApiToken:
    """A user's token may name only projects assigned to that user (403 otherwise). It
    stops working while the user is deactivated and loses a project the user loses."""
    return tokens.create_token(
        session,
        body.name,
        body.project_ids,
        body.permissions,
        body.project_permissions,
        owner_user_id=acting_user_id(member),
    )


@router.patch("/{token_id}")
def update_token(
    token_id: str, body: TokenUpdate, session: SessionDep, member: MemberDep
) -> Item[ApiTokenSummary]:
    """For a user, a token of somebody else answers 404."""
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    return Item(
        item=tokens.update_token(session, token_id, **changes, owner_user_id=acting_user_id(member))
    )


@router.post("/{token_id}/revoke")
def revoke_token(token_id: str, session: SessionDep, member: MemberDep) -> Item[ApiTokenSummary]:
    """For a user, a token of somebody else answers 404."""
    return Item(item=tokens.revoke_token(session, token_id, acting_user_id(member)))
