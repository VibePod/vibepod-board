"""The admin's side of an OAuth sign-in: what a waiting request is, and the decision on it."""

from fastapi import APIRouter

from vibepod_board.api.models import OAuthApproval
from vibepod_board.api.responses import Item, Redirect
from vibepod_board.auth import AdminDep
from vibepod_board.db import SessionDep
from vibepod_board.schemas import OAuthRequestInfo
from vibepod_board.services import oauth

router = APIRouter(prefix="/api/oauth", tags=["oauth"])


@router.get("/requests/{request_id}")
def get_request(request_id: str, session: SessionDep, _admin: AdminDep) -> Item[OAuthRequestInfo]:
    """The client and redirect address of a sign-in that waits for a decision. Answers 404
    once the request was decided or expired."""
    return Item(item=oauth.pending_request(session, request_id))


@router.post("/requests/{request_id}/approve")
def approve_request(
    request_id: str, body: OAuthApproval, session: SessionDep, _admin: AdminDep
) -> Redirect:
    """Approves the sign-in for the given projects. `redirectTo` takes the browser back to
    the client with a one-time code."""
    return Redirect(redirect_to=oauth.approve_request(session, request_id, body.project_ids))


@router.post("/requests/{request_id}/deny")
def deny_request(request_id: str, session: SessionDep, _admin: AdminDep) -> Redirect:
    """Refuses the sign-in. `redirectTo` takes the browser back to the client with
    `error=access_denied`."""
    return Redirect(redirect_to=oauth.deny_request(session, request_id))
