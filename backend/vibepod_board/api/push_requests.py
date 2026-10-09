from fastapi import APIRouter, Request

from vibepod_board.api.github import get_github_client
from vibepod_board.api.models import ClaimPushRequest, PushRequestCreate, PushResult
from vibepod_board.auth import AccessDep
from vibepod_board.db import SessionDep
from vibepod_board.schemas import PushRequest
from vibepod_board.services import push_requests

router = APIRouter(prefix="/api/board", tags=["board"])


@router.post("/push-requests/{request_id}/claim")
def claim_push_request(
    request_id: str, body: ClaimPushRequest, session: SessionDep, access: AccessDep
) -> PushRequest:
    """Takes a pending push request with a 5 minute lease. Refused (409) while someone else
    holds it, such as the reviewer it was made for, and once it ended."""
    return push_requests.claim_push_request(session, access, request_id, body.assignee)


@router.post("/push-requests/{request_id}/result")
def report_push_result(
    request_id: str, body: PushResult, request: Request, session: SessionDep, access: AccessDep
) -> PushRequest:
    """The holder reports the push: `pushed` (with `pushedSha`) records the commit on the
    card and opens the pull request the request carries; `failed` (with `error`) ends it."""
    # Opening the pull request needs GitHub; without a token the request reports why.
    client = get_github_client(request) if request.app.state.settings.github_token else None
    return push_requests.report_result(
        session,
        access,
        request_id,
        body.assignee,
        body.status,
        pushed_sha=body.pushed_sha,
        error=body.error,
        client=client,
    )


@router.post("/{card_id}/push-request")
def request_push(
    card_id: str, session: SessionDep, access: AccessDep, body: PushRequestCreate | None = None
) -> PushRequest:
    """Asks the project's workers to push the card's branch; with `openPr` the board opens
    that pull request once it is pushed. A request still pending or claimed for the card is
    returned instead of a new one, with these PR options added."""
    open_pr = body.open_pr.model_dump(by_alias=False) if body and body.open_pr else None
    return push_requests.request_push(session, access, card_id, open_pr)


@router.get("/{card_id}/push-request")
def latest_push_request(card_id: str, session: SessionDep, access: AccessDep) -> PushRequest:
    """The card's latest push request; 404 when it never had one."""
    return push_requests.latest_push_request(session, access, card_id)
