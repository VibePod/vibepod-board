"""FastMCP server at /mcp.

Clients authenticate with the same project-scoped `vbp_…` tokens as the REST API.
`BoardTokenVerifier` resolves a token to its projects; every tool then runs with that
token's access. Tool names and arguments match the TypeScript server, so existing agent
configurations keep working.
"""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

import anyio
import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from pydantic import BaseModel, Field
from sqlmodel import Session

from vibepod_board.access import AccessContext, require_admin, token_access
from vibepod_board.api.github import github_client
from vibepod_board.config import Settings
from vibepod_board.db import get_engine
from vibepod_board.enums import BoardColumn, DocumentKind, IdeaStatus
from vibepod_board.errors import BoardError
from vibepod_board.github import GitHubClient
from vibepod_board.services import (
    board,
    dependencies,
    documents,
    github_sync,
    ideas,
    projects,
    readiness,
    state,
    tokens,
)

TaskId = Annotated[str, Field(min_length=1)]
ProjectFilter = Annotated[str | None, Field(description="Limit to one project.")]
Score = Annotated[int, Field(ge=1, le=10)]


class BoardTokenVerifier(TokenVerifier):
    """Accepts unrevoked board API tokens and carries their project scope as a claim."""

    async def verify_token(self, token: str) -> AccessToken | None:
        def authenticate() -> tokens.AuthenticatedToken | None:
            with Session(get_engine()) as session:
                return tokens.authenticate_token(session, token)

        authenticated = await anyio.to_thread.run_sync(authenticate)
        if authenticated is None:
            return None
        return AccessToken(
            token=token,
            client_id=authenticated.token_id,
            scopes=[],
            claims={"project_ids": authenticated.project_ids},
        )


def current_access() -> AccessContext:
    access_token = get_access_token()
    if access_token is None:
        raise ToolError("Authentication required")
    return token_access(access_token.client_id, access_token.claims.get("project_ids", []))


def _json(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    return value


def run(operation: Callable[[Session, AccessContext], Any]) -> dict[str, Any]:
    """Runs a service call with the caller's access in its own session.

    Tools are sync and FastMCP runs them in a worker thread, so blocking database access
    does not stall the event loop.
    """
    access = current_access()
    try:
        with Session(get_engine(), expire_on_commit=False) as session:
            return _json(operation(session, access))
    except BoardError as error:
        raise ToolError(error.message) from error


def create_mcp_server(
    settings: Settings, github_transport: httpx.BaseTransport | None = None
) -> FastMCP:
    mcp = FastMCP("vibepod-board", version="0.1.0", auth=BoardTokenVerifier())

    def github() -> GitHubClient:
        try:
            return github_client(settings, github_transport)
        except BoardError as error:
            raise ToolError(error.message) from error

    @mcp.resource(
        "vibepod-board://state",
        name="board-state",
        title="VibePod Board State",
        description="Full scoped vibepod-board state as JSON.",
        mime_type="application/json",
    )
    def board_state() -> str:
        return json.dumps(run(state.board_state), indent=2)

    @mcp.tool(
        title="List Projects",
        description="List projects that contain tasks, board cards, and notes.",
    )
    def list_projects() -> dict[str, Any]:
        return run(lambda s, a: {"items": projects.list_projects(s, a)})

    @mcp.tool(
        title="Create Project",
        description="Create a project container for tasks, board cards, and notes.",
    )
    def create_project(
        key: Annotated[str, Field(pattern=r"^[A-Z]{1,3}$")],
        title: Annotated[str, Field(min_length=1)],
        summary: str | None = None,
    ) -> dict[str, Any]:
        def operation(session: Session, access: AccessContext) -> dict[str, Any]:
            require_admin(access)
            return {"item": projects.create_project(session, key, title, summary)}

        return run(operation)

    @mcp.tool(
        title="List Ideas",
        description="List ideas with refinement and readiness state.",
    )
    def list_ideas(projectId: ProjectFilter = None) -> dict[str, Any]:  # noqa: N803
        return run(lambda s, a: {"items": ideas.list_ideas(s, a, projectId)})

    @mcp.tool(title="Create Idea", description="Create a new idea for refinement.")
    def create_idea(
        title: Annotated[str, Field(min_length=1)],
        projectId: str | None = None,  # noqa: N803
        summary: str | None = None,
        details: str | None = None,
        labels: list[str] | None = None,
        acceptanceCriteria: list[str] | None = None,  # noqa: N803
        dependsOn: Annotated[  # noqa: N803
            list[str] | None,
            Field(description="Ids of tasks in the same project that must be done first."),
        ] = None,
        repositoryLocalPath: str | None = None,  # noqa: N803
        repositoryRemoteUrl: str | None = None,  # noqa: N803
    ) -> dict[str, Any]:
        return run(
            lambda s, a: {
                "item": ideas.create_idea(
                    s,
                    a,
                    title=title,
                    project_id=projectId,
                    summary=summary,
                    details=details,
                    labels=labels,
                    acceptance_criteria=acceptanceCriteria,
                    depends_on=dependsOn,
                    repository_local_path=repositoryLocalPath,
                    repository_remote_url=repositoryRemoteUrl,
                )
            }
        )

    @mcp.tool(
        title="Update Idea",
        description="Edit an existing idea's fields. Only provided fields change; omitted "
        "fields are left as-is.",
    )
    def update_idea(
        id: TaskId,
        title: Annotated[str | None, Field(min_length=1)] = None,
        summary: str | None = None,
        details: str | None = None,
        labels: list[str] | None = None,
        acceptanceCriteria: list[str] | None = None,  # noqa: N803
        dependsOn: Annotated[  # noqa: N803
            list[str] | None,
            Field(description="Replaces the full set of blocking task ids when given."),
        ] = None,
        repositoryLocalPath: str | None = None,  # noqa: N803
        repositoryRemoteUrl: str | None = None,  # noqa: N803
        githubIssueUrl: Annotated[  # noqa: N803
            str | None,
            Field(
                description="Link the task to a GitHub issue "
                "(https://github.com/owner/repo/issues/123); an empty string unlinks it."
            ),
        ] = None,
        status: IdeaStatus | None = None,
    ) -> dict[str, Any]:
        return run(
            lambda s, a: {
                "item": ideas.update_idea(
                    s,
                    a,
                    id,
                    title=title,
                    summary=summary,
                    details=details,
                    labels=labels,
                    acceptance_criteria=acceptanceCriteria,
                    depends_on=dependsOn,
                    status=status,
                    repository_local_path=repositoryLocalPath,
                    repository_remote_url=repositoryRemoteUrl,
                    github_issue_url=githubIssueUrl,
                )
            }
        )

    @mcp.tool(
        title="Delete Task",
        description="Permanently delete a task with its board card, dependency edges and "
        "readiness history; documents stop linking to it. This cannot be undone — to reject "
        "work but keep the record, set status to denied with update_idea instead. A linked "
        "GitHub issue is not touched. Returns the ids of tasks that depended on it.",
    )
    def delete_idea(id: TaskId) -> dict[str, Any]:
        return run(lambda s, a: ideas.delete_idea(s, a, id))

    @mcp.tool(
        title="Push Task to GitHub",
        description="Create the task's GitHub issue, or update its title, body and labels "
        "when the task is already linked. Fails when the issue changed on GitHub since the "
        "last sync; pull first in that case. Never opens or closes the issue.",
    )
    def push_github_issue(id: TaskId) -> dict[str, Any]:
        client = github()
        return run(
            lambda s, a: {"item": github_sync.push(s, a, id, client, settings.github_repository)}
        )

    @mcp.tool(
        title="Pull Task from GitHub",
        description="Refresh a linked task from its GitHub issue: title, labels, state and "
        "sync time. Details are only filled when empty; status and board column never "
        "change.",
    )
    def pull_github_issue(id: TaskId) -> dict[str, Any]:
        client = github()
        return run(lambda s, a: {"item": github_sync.pull(s, a, id, client)})

    @mcp.tool(
        title="Upsert GitHub Issue",
        description="Import a GitHub issue you fetched yourself: creates a task for it, or "
        "refreshes the task already linked to the same repository and number in the "
        "project. Skipped (unchanged: true) when remoteUpdatedAt is not newer than the "
        "last sync. Details of an existing task are never overwritten.",
    )
    def upsert_github_issue(
        repository: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")],
        number: Annotated[int, Field(gt=0)],
        url: Annotated[str, Field(min_length=1)],
        title: Annotated[str, Field(min_length=1)],
        state: Literal["open", "closed"],
        remoteUpdatedAt: datetime,  # noqa: N803
        body: str | None = None,
        labels: list[str] | None = None,
        projectId: str | None = None,  # noqa: N803
    ) -> dict[str, Any]:
        return run(
            lambda s, a: github_sync.upsert_issue(
                s,
                a,
                repository=repository,
                number=number,
                url=url,
                title=title,
                state=state,
                remote_updated_at=remoteUpdatedAt,
                body=body or "",
                labels=labels or [],
                project_id=projectId,
            )
        )

    @mcp.tool(
        title="Add Task Dependency",
        description="Make a task depend on another task in the same project. The dependency "
        "must be finished (board column done) or denied before the task is unblocked. "
        "Cycles are rejected.",
    )
    def add_idea_dependency(
        id: Annotated[str, Field(min_length=1, description="Task that is blocked.")],
        dependsOnId: Annotated[  # noqa: N803
            str, Field(min_length=1, description="Task that must finish first.")
        ],
    ) -> dict[str, Any]:
        return run(lambda s, a: {"item": dependencies.add_dependency(s, a, id, dependsOnId)})

    @mcp.tool(
        title="Remove Task Dependency",
        description="Drop one dependency edge between two tasks.",
    )
    def remove_idea_dependency(id: TaskId, dependsOnId: TaskId) -> dict[str, Any]:  # noqa: N803
        return run(lambda s, a: {"item": dependencies.remove_dependency(s, a, id, dependsOnId)})

    @mcp.tool(
        title="Set Task Dependencies",
        description="Replace the full set of tasks a task depends on. Pass an empty list to "
        "clear all dependencies.",
    )
    def set_idea_dependencies(id: TaskId, dependsOnIds: list[str]) -> dict[str, Any]:  # noqa: N803
        return run(lambda s, a: {"item": dependencies.set_dependencies(s, a, id, dependsOnIds)})

    @mcp.tool(
        title="List Work Order",
        description="List tasks in dependency-resolved execution order. Each item reports "
        "position, wave (0 = no dependencies), blockedBy, isBlocked, isComplete, and "
        "isActionable. Work items with isActionable true can be started now; anything in "
        "cyclicTaskIds could not be ordered.",
    )
    def list_work_order(projectId: ProjectFilter = None) -> dict[str, Any]:  # noqa: N803
        return run(lambda s, a: ideas.work_order(s, a, projectId))

    @mcp.tool(
        title="Mark Idea Ready",
        description="Mark an idea as ready and available on the Kanban board.",
    )
    def mark_idea_ready(id: TaskId) -> dict[str, Any]:
        return run(lambda s, a: {"item": ideas.mark_ready(s, a, id)})

    @mcp.tool(title="List Board", description="Read the Kanban board columns.")
    def list_board(projectId: ProjectFilter = None) -> dict[str, Any]:  # noqa: N803
        return run(lambda s, a: {"columns": board.board_columns(s, a, projectId)})

    @mcp.tool(
        title="Move Board Card",
        description="Move a board card to another Kanban column.",
    )
    def move_board_card(id: TaskId, column: BoardColumn) -> dict[str, Any]:
        return run(lambda s, a: {"item": board.move_card(s, a, id, column)})

    @mcp.tool(
        title="Update Board Card",
        description="Edit board-card metadata such as implementation branch and repository. "
        "Only provided fields change.",
    )
    def update_board_card(
        id: TaskId,
        column: BoardColumn | None = None,
        branchName: str | None = None,  # noqa: N803
        details: str | None = None,
        repositoryLocalPath: str | None = None,  # noqa: N803
        repositoryRemoteUrl: str | None = None,  # noqa: N803
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {
            "column": column,
            "details": details,
            **{
                key: value
                for key, value in {
                    "branch_name": branchName,
                    "repository_local_path": repositoryLocalPath,
                    "repository_remote_url": repositoryRemoteUrl,
                }.items()
                if value is not None
            },
        }
        return run(lambda s, a: {"item": board.update_card(s, a, id, **changes)})

    @mcp.tool(
        title="Set Card Readiness",
        description="Record an LLM-evaluated readiness score (1-10) with a short reason on a "
        "board card. Does not change updated_at, so a later content edit marks the score "
        "stale.",
    )
    def set_card_readiness(
        id: TaskId, score: Score, reason: Annotated[str, Field(min_length=1)]
    ) -> dict[str, Any]:
        return run(lambda s, a: {"item": readiness.set_card_readiness(s, a, id, score, reason)})

    @mcp.tool(
        title="Set Idea Readiness",
        description="Record an LLM-evaluated readiness score (1-10) with a short reason on an "
        "idea; mirrors onto its linked board card. Does not change updated_at, so a later "
        "content edit marks the score stale.",
    )
    def set_idea_readiness(
        id: TaskId, score: Score, reason: Annotated[str, Field(min_length=1)]
    ) -> dict[str, Any]:
        return run(lambda s, a: {"item": readiness.set_idea_readiness(s, a, id, score, reason)})

    @mcp.tool(
        title="List Idea Readiness History",
        description="List an idea's readiness rating history, newest first (score, reason, date).",
    )
    def list_idea_readiness(id: TaskId) -> dict[str, Any]:
        return run(lambda s, a: {"items": readiness.list_idea_readiness(s, a, id)})

    @mcp.tool(
        title="Create Document",
        description="Create an execution plan, design doc, or notes document.",
    )
    def create_document(
        title: Annotated[str, Field(min_length=1)],
        projectId: str | None = None,  # noqa: N803
        kind: DocumentKind | None = None,
        content: str | None = None,
        linkedIdeaIds: list[str] | None = None,  # noqa: N803
        linkedCardIds: list[str] | None = None,  # noqa: N803
    ) -> dict[str, Any]:
        return run(
            lambda s, a: {
                "item": documents.create_document(
                    s,
                    a,
                    title=title,
                    project_id=projectId,
                    kind=kind,
                    content=content,
                    linked_idea_ids=linkedIdeaIds,
                    linked_card_ids=linkedCardIds,
                )
            }
        )

    @mcp.tool(
        title="Update Document",
        description="Edit an existing document's fields. Only provided fields change; "
        "omitted fields are left as-is.",
    )
    def update_document(
        id: TaskId,
        title: Annotated[str | None, Field(min_length=1)] = None,
        kind: DocumentKind | None = None,
        content: str | None = None,
        linkedIdeaIds: list[str] | None = None,  # noqa: N803
        linkedCardIds: list[str] | None = None,  # noqa: N803
    ) -> dict[str, Any]:
        return run(
            lambda s, a: {
                "item": documents.update_document(
                    s,
                    a,
                    id,
                    title=title,
                    kind=kind,
                    content=content,
                    linked_idea_ids=linkedIdeaIds,
                    linked_card_ids=linkedCardIds,
                )
            }
        )

    @mcp.tool(
        title="List Documents",
        description="List execution plans and other planning documents.",
    )
    def list_documents(projectId: ProjectFilter = None) -> dict[str, Any]:  # noqa: N803
        return run(lambda s, a: {"items": documents.list_documents(s, a, projectId)})

    return mcp
