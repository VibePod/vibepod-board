"""The admin's side of an OAuth sign-in: the consent page, what a waiting request is, and
the decision on it."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from vibepod_board.api.models import OAuthApproval
from vibepod_board.api.responses import Item, Redirect
from vibepod_board.auth import AdminDep
from vibepod_board.db import SessionDep
from vibepod_board.schemas import OAuthRequestInfo
from vibepod_board.services import oauth

router = APIRouter(tags=["oauth"])

PAGE = Path(__file__).resolve().parents[1] / "static" / "oauth-consent.html"
# The page asks for the admin password and a decision, so it must never run inside another
# site's frame, and a stale copy must not outlive the request it was opened for.
PAGE_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


@router.get("/oauth/consent", include_in_schema=False)
def consent_page() -> FileResponse:
    """Where `/authorize` sends the browser; the page reads the request id from its URL."""
    return FileResponse(PAGE, media_type="text/html", headers=PAGE_HEADERS)


@router.get("/api/oauth/requests/{request_id}")
def get_request(request_id: str, session: SessionDep, _admin: AdminDep) -> Item[OAuthRequestInfo]:
    """The client and redirect address of a sign-in that waits for a decision. Answers 404
    once the request was decided or expired."""
    return Item(item=oauth.pending_request(session, request_id))


@router.post("/api/oauth/requests/{request_id}/approve")
def approve_request(
    request_id: str, body: OAuthApproval, session: SessionDep, _admin: AdminDep
) -> Redirect:
    """Approves the sign-in for the given projects and permissions; without `permissions`
    the token gets full access. `redirectTo` takes the browser back to the client with a
    one-time code."""
    return Redirect(
        redirect_to=oauth.approve_request(session, request_id, body.project_ids, body.permissions)
    )


@router.post("/api/oauth/requests/{request_id}/deny")
def deny_request(request_id: str, session: SessionDep, _admin: AdminDep) -> Redirect:
    """Refuses the sign-in. `redirectTo` takes the browser back to the client with
    `error=access_denied`."""
    return Redirect(redirect_to=oauth.deny_request(session, request_id))
