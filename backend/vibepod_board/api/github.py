from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request

from vibepod_board.api.responses import Item
from vibepod_board.auth import AccessDep
from vibepod_board.config import Settings
from vibepod_board.db import SessionDep
from vibepod_board.errors import BoardError
from vibepod_board.github import GitHubClient
from vibepod_board.schemas import ApiModel, BoardCard, Idea
from vibepod_board.services import github_sync, pull_requests

router = APIRouter(prefix="/api", tags=["github"])


class GitHubDisabled(BoardError):
    status_code = 503


class GitHubStatus(ApiModel):
    enabled: bool
    default_repository: str | None = None


def github_client(settings: Settings, transport: httpx.BaseTransport | None) -> GitHubClient:
    if not settings.github_token:
        raise GitHubDisabled("GitHub sync is disabled: set GITHUB_TOKEN")
    return GitHubClient(settings.github_token, transport)


def get_github_client(request: Request) -> GitHubClient:
    return github_client(request.app.state.settings, request.app.state.github_transport)


ClientDep = Annotated[GitHubClient, Depends(get_github_client)]


@router.get("/github")
def github_status(request: Request, _access: AccessDep) -> GitHubStatus:
    settings: Settings = request.app.state.settings
    return GitHubStatus(
        enabled=bool(settings.github_token), default_repository=settings.github_repository
    )


@router.post("/ideas/{idea_id}/github/push")
def push_issue(
    idea_id: str, request: Request, session: SessionDep, access: AccessDep, client: ClientDep
) -> Item[Idea]:
    default_repository = request.app.state.settings.github_repository
    return Item(item=github_sync.push(session, access, idea_id, client, default_repository))


@router.post("/ideas/{idea_id}/github/pull")
def pull_issue(
    idea_id: str, session: SessionDep, access: AccessDep, client: ClientDep
) -> Item[Idea]:
    return Item(item=github_sync.pull(session, access, idea_id, client))


class OpenPullRequest(ApiModel):
    title: str | None = None
    base: str | None = None
    body: str | None = None
    draft: bool = False


class LinkPullRequest(ApiModel):
    url: str


@router.get("/board/{card_id}/pull-request")
def pull_request_draft(
    card_id: str, session: SessionDep, access: AccessDep, client: ClientDep
) -> pull_requests.PullRequestDraft:
    return pull_requests.pull_request_draft(session, access, card_id, client)


@router.post("/board/{card_id}/pull-request")
def open_pull_request(
    card_id: str, body: OpenPullRequest, session: SessionDep, access: AccessDep, client: ClientDep
) -> Item[BoardCard]:
    return Item(
        item=pull_requests.open_pull_request(session, access, card_id, client, **body.model_dump())
    )


@router.post("/board/{card_id}/pull-request/link")
def link_pull_request(
    card_id: str, body: LinkPullRequest, request: Request, session: SessionDep, access: AccessDep
) -> Item[BoardCard]:
    # Linking works without a token too; the PR's state is only read when there is one.
    client = (
        get_github_client(request)
        if request.app.state.settings.github_token and body.url.strip()
        else None
    )
    return Item(item=pull_requests.link_pull_request(session, access, card_id, body.url, client))
