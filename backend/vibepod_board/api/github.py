from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request

from vibepod_board.api.responses import Item
from vibepod_board.auth import AccessDep
from vibepod_board.config import Settings
from vibepod_board.db import SessionDep
from vibepod_board.errors import BoardError
from vibepod_board.github import GitHubClient
from vibepod_board.schemas import ApiModel, Idea
from vibepod_board.services import github_sync

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
