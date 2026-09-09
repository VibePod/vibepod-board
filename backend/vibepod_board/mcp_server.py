"""FastMCP server at /mcp.

Clients authenticate with the same project-scoped `vbp_…` tokens as the REST API.
`BoardTokenVerifier` resolves a token to its projects; every tool then runs with that
token's access. Tool names and arguments match the TypeScript server, so existing agent
configurations keep working.
"""

import json
from collections.abc import Callable
from typing import Annotated, Any, Literal

import anyio
import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from pydantic import AwareDatetime, BaseModel, Field
from sqlmodel import Session

from vibepod_board.access import AccessContext, require_admin, token_access
from vibepod_board.api.github import github_client
from vibepod_board.api.models import BoardCardBatchItem, IdeaBatchItem
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
from vibepod_board.services.listing import BATCH_LIMIT
from vibepod_board.services.views import View, project_cards, project_documents, project_ideas

TaskId = Annotated[
    str,
    Field(
        min_length=1,
        description="Task id, key such as VP-236, or bare task number when the token covers "
        "one project.",
    ),
]
CardId = Annotated[
    str,
    Field(min_length=1, description="Card id, or the key of its task such as VP-236."),
]
ProjectFilter = Annotated[
    str | None, Field(description="Limit to one project, by id, key or title.")
]
Score = Annotated[int, Field(ge=1, le=10)]
ViewArg = Annotated[
    View | None,
    Field(
        description="ref (id, key, updatedAt), compact (omits free text, names dependencies "
        "by key) or full (the whole record)."
    ),
]
UpdatedSince = Annotated[
    str | None,
    Field(description="ISO timestamp with a UTC offset; returns only records changed after it."),
]
AssigneeFilter = Annotated[
    list[Annotated[str, Field(min_length=1)]] | None,
    Field(description="Holders to match exactly, such as Claude::Subagent101."),
]
Unassigned = Annotated[
    bool | None,
    Field(
        description="True also returns work nobody holds; with assignee the two widen each other."
    ),
]
Assignee = Annotated[
    str | None,
    Field(
        description="Free text naming who holds the task, such as "
        "Claude::Subagent101::Worktree12. An empty string releases it."
    ),
]
ExpectedUpdatedAt = Annotated[
    str | None,
    Field(
        min_length=1,
        description="Refuse the write if the record changed since this updatedAt.",
    ),
]
MCP_LIST_LIMIT = 200
READ_ONLY = {"readOnlyHint": True}
IDEMPOTENT = {"idempotentHint": True}


class ReadinessInput(BaseModel):
    score: Score
    reason: Annotated[str, Field(min_length=1)]


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
        annotations=READ_ONLY,
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

    def echo_idea(session: Session, idea: Any, view: View | None) -> dict[str, Any]:
        """Writes echo a compact record: the caller already knows what it sent."""
        return {"item": project_ideas(session, [idea], view or View.COMPACT)[0]}

    def echo_card(session: Session, card: Any, view: View | None) -> dict[str, Any]:
        return {"item": project_cards(session, [card], view or View.COMPACT)[0]}

    @mcp.tool(
        title="List Ideas",
        description="List tasks with refinement and readiness state. Accepts a project id, key "
        "or title, and filters by status, assignee or modification time. Returns compact "
        "records unless view says otherwise; pass nextCursor back as cursor to continue.",
        annotations=READ_ONLY,
    )
    def list_ideas(
        projectId: ProjectFilter = None,  # noqa: N803
        status: list[IdeaStatus] | None = None,
        updatedSince: UpdatedSince = None,  # noqa: N803
        assignee: AssigneeFilter = None,
        unassigned: Unassigned = None,
        view: ViewArg = None,
        limit: Annotated[
            int | None,
            Field(ge=1, le=500, description="Defaults to 200; pass nextCursor to continue."),
        ] = None,
        cursor: Annotated[str | None, Field(min_length=1)] = None,
    ) -> dict[str, Any]:
        def operation(session: Session, access: AccessContext) -> dict[str, Any]:
            chosen = view or View.COMPACT
            filters = ideas.IdeaFilter(
                project=projectId,
                status=status or [],
                updated_since=updatedSince,
                assignee=assignee or [],
                unassigned=bool(unassigned),
                # MCP lists are capped by default; REST stays unlimited.
                limit=limit or MCP_LIST_LIMIT,
                cursor=cursor,
            )
            page = ideas.list_ideas_page(session, access, filters)
            cards = (
                board.list_cards(session, access, filters=board.CardFilter(project=projectId))
                if chosen == View.COMPACT
                else None
            )
            result: dict[str, Any] = {"items": project_ideas(session, page.items, chosen, cards)}
            if page.next_cursor:
                result["nextCursor"] = page.next_cursor
            return result

        return run(operation)

    @mcp.tool(
        title="Get Task",
        description="Read one task by reference: its id, its key such as VP-236, or a bare "
        "task number when the token covers one project. Returns the full record by default.",
        annotations=READ_ONLY,
    )
    def get_idea(idea: TaskId, view: ViewArg = None) -> dict[str, Any]:
        return run(
            lambda s, a: {
                "item": project_ideas(s, [ideas.get_idea(s, a, idea)], view or View.FULL)[0]
            }
        )

    @mcp.tool(
        title="Get Board Card",
        description="Read one board card by reference: its id, or the key of the task it "
        "belongs to such as VP-236. Returns the full record by default.",
        annotations=READ_ONLY,
    )
    def get_board_card(card: CardId, view: ViewArg = None) -> dict[str, Any]:
        return run(
            lambda s, a: {
                "item": project_cards(s, [board.get_card(s, a, card)], view or View.FULL)[0]
            }
        )

    @mcp.tool(
        title="Create Idea",
        description="Create a task for refinement. The project accepts an id, key or title; "
        "omitting it uses the token's project. Echoes a compact record unless view says "
        "otherwise.",
    )
    def create_idea(
        title: Annotated[str, Field(min_length=1)],
        projectId: str | None = None,  # noqa: N803
        summary: str | None = None,
        details: str | None = None,
        labels: list[str] | None = None,
        acceptanceCriteria: list[str] | None = None,  # noqa: N803
        dependsOn: Annotated[  # noqa: N803
            list[str] | None,
            Field(description="Tasks in the same project that must be done first, by id or key."),
        ] = None,
        repositoryLocalPath: str | None = None,  # noqa: N803
        repositoryRemoteUrl: str | None = None,  # noqa: N803
        assignee: Assignee = None,
        view: ViewArg = None,
    ) -> dict[str, Any]:
        return run(
            lambda s, a: echo_idea(
                s,
                ideas.create_idea(
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
                    assignee=assignee,
                ),
                view,
            )
        )

    @mcp.tool(
        title="Update Idea",
        description="Edit one task. Accepts an id, a key such as VP-236, or a bare task number "
        "when the token covers one project. Only provided fields change. Setting status to "
        "ready puts the task on the board; onBoard false removes its card. Pass readiness to "
        "record a score in the same write, and expectedUpdatedAt to refuse the write if the "
        "task changed since you read it. To claim a task safely, write assignee with the "
        "expectedUpdatedAt you read, so a competing claim is refused rather than overwritten. "
        "Echoes a compact record unless view says otherwise.",
        annotations=IDEMPOTENT,
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
            Field(description="Replaces the full set of blocking tasks (ids or keys) when given."),
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
        assignee: Assignee = None,
        status: IdeaStatus | None = None,
        onBoard: Annotated[  # noqa: N803
            bool | None,
            Field(description="True puts the task on the Kanban board, false removes its card."),
        ] = None,
        readiness: Annotated[
            ReadinessInput | None, Field(description="Records a readiness score in the same write.")
        ] = None,
        expectedUpdatedAt: ExpectedUpdatedAt = None,  # noqa: N803
        view: ViewArg = None,
    ) -> dict[str, Any]:
        return run(
            lambda s, a: echo_idea(
                s,
                ideas.update_idea(
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
                    assignee=assignee,
                    on_board=onBoard,
                    readiness=readiness,
                    expected_updated_at=expectedUpdatedAt,
                ),
                view,
            )
        )

    @mcp.tool(
        title="Update Tasks",
        description=f"Apply up to {BATCH_LIMIT} task writes in one transaction. Each item is an "
        "update_idea input, including its own optional expectedUpdatedAt. All-or-nothing: one "
        "failing item rolls the whole batch back and names its position. Returns bare "
        "references unless view says otherwise.",
    )
    def update_ideas(
        items: Annotated[list[IdeaBatchItem], Field(max_length=BATCH_LIMIT)],
        view: ViewArg = None,
    ) -> dict[str, Any]:
        changes = [item.model_dump(exclude_unset=True, by_alias=False) for item in items]
        return run(
            lambda s, a: {
                "items": project_ideas(s, ideas.update_ideas(s, a, changes), view or View.REF)
            }
        )

    @mcp.tool(
        title="Update Board Cards",
        description=f"Apply up to {BATCH_LIMIT} card writes in one transaction, moving and "
        "editing in the same call. All-or-nothing, same as update_ideas.",
    )
    def update_board_cards(
        items: Annotated[list[BoardCardBatchItem], Field(max_length=BATCH_LIMIT)],
        view: ViewArg = None,
    ) -> dict[str, Any]:
        changes = [item.model_dump(exclude_unset=True, by_alias=False) for item in items]
        return run(
            lambda s, a: {
                "items": project_cards(s, board.update_cards(s, a, changes), view or View.REF)
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
        url: Annotated[
            str,
            Field(
                min_length=1,
                description="The issue's html_url; must name the same repository and number.",
            ),
        ],
        title: Annotated[str, Field(min_length=1)],
        state: Literal["open", "closed"],
        remoteUpdatedAt: Annotated[  # noqa: N803
            AwareDatetime, Field(description="The issue's updated_at, with a UTC offset.")
        ],
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
        "Cycles are rejected. Both tasks accept an id or a key such as VP-236.",
    )
    def add_idea_dependency(
        id: Annotated[str, Field(min_length=1, description="Task that is blocked.")],
        dependsOnId: Annotated[  # noqa: N803
            str, Field(min_length=1, description="Task that must finish first.")
        ],
        view: ViewArg = None,
    ) -> dict[str, Any]:
        return run(
            lambda s, a: echo_idea(s, dependencies.add_dependency(s, a, id, dependsOnId), view)
        )

    @mcp.tool(
        title="Remove Task Dependency",
        description="Drop one dependency edge between two tasks.",
    )
    def remove_idea_dependency(
        id: TaskId,
        dependsOnId: TaskId,  # noqa: N803
        view: ViewArg = None,
    ) -> dict[str, Any]:
        return run(
            lambda s, a: echo_idea(s, dependencies.remove_dependency(s, a, id, dependsOnId), view)
        )

    @mcp.tool(
        title="Set Task Dependencies",
        description="Replace the full set of tasks a task depends on. Pass an empty list to "
        "clear all dependencies.",
    )
    def set_idea_dependencies(
        id: TaskId,
        dependsOnIds: list[str],  # noqa: N803
        view: ViewArg = None,
    ) -> dict[str, Any]:
        return run(
            lambda s, a: echo_idea(s, dependencies.set_dependencies(s, a, id, dependsOnIds), view)
        )

    @mcp.tool(
        title="List Work Order",
        description="List tasks in dependency-resolved execution order. Each item reports "
        "position, wave (0 = no dependencies), blockedBy, isBlocked, isComplete, and "
        "isActionable. Work items with isActionable true can be started now; anything in "
        "cyclicTaskIds could not be ordered.",
        annotations=READ_ONLY,
    )
    def list_work_order(projectId: ProjectFilter = None) -> dict[str, Any]:  # noqa: N803
        return run(lambda s, a: ideas.work_order(s, a, projectId))

    @mcp.tool(
        title="Mark Idea Ready",
        description="Mark a task ready and put it on the Kanban board. Accepts an id or a key "
        "such as VP-236, and an optional readiness score recorded in the same write.",
    )
    def mark_idea_ready(
        id: TaskId, readiness: ReadinessInput | None = None, view: ViewArg = None
    ) -> dict[str, Any]:
        return run(lambda s, a: echo_idea(s, ideas.mark_ready(s, a, id, readiness), view))

    @mcp.tool(
        title="List Board",
        description="Read the Kanban board columns. Accepts a project id, key or title, and "
        "filters by column, assignee or modification time. Archived cards are left out; see "
        "list_archived_cards. Returns compact cards unless view says otherwise.",
        annotations=READ_ONLY,
    )
    def list_board(
        projectId: ProjectFilter = None,  # noqa: N803
        column: list[BoardColumn] | None = None,
        updatedSince: UpdatedSince = None,  # noqa: N803
        assignee: AssigneeFilter = None,
        unassigned: Unassigned = None,
        view: ViewArg = None,
    ) -> dict[str, Any]:
        def operation(session: Session, access: AccessContext) -> dict[str, Any]:
            filters = board.CardFilter(
                project=projectId,
                column=column or [],
                updated_since=updatedSince,
                assignee=assignee or [],
                unassigned=bool(unassigned),
            )
            columns = board.board_columns(session, access, filters=filters)
            return {
                "columns": {
                    name: project_cards(session, cards, view or View.COMPACT)
                    for name, cards in dict(columns).items()
                }
            }

        return run(operation)

    @mcp.tool(
        title="Move Board Card",
        description="Deprecated: use update_board_card, which moves and edits in one call. "
        "Move a board card to another Kanban column.",
        annotations=IDEMPOTENT,
    )
    def move_board_card(id: CardId, column: BoardColumn, view: ViewArg = None) -> dict[str, Any]:
        return run(lambda s, a: echo_card(s, board.move_card(s, a, id, column), view))

    @mcp.tool(
        title="Archive Board Card",
        description="Archive a card from the done column: it leaves the board (list_board) "
        "but keeps its task, dependencies, readiness history and GitHub link, and still "
        "counts as done. Cards in other columns are rejected.",
    )
    def archive_board_card(id: TaskId) -> dict[str, Any]:
        return run(lambda s, a: {"item": board.archive_card(s, a, id)})

    @mcp.tool(
        title="Unarchive Board Card",
        description="Put an archived card back on the board in the done column.",
    )
    def unarchive_board_card(id: TaskId) -> dict[str, Any]:
        return run(lambda s, a: {"item": board.unarchive_card(s, a, id)})

    @mcp.tool(
        title="List Archived Cards",
        description="List archived board cards, most recently archived first.",
    )
    def list_archived_cards(projectId: ProjectFilter = None) -> dict[str, Any]:  # noqa: N803
        return run(lambda s, a: {"items": board.list_archived_cards(s, a, projectId)})

    @mcp.tool(
        title="Update Board Card",
        description="Edit one board card, moving and editing in the same call. Accepts a card "
        "id or the key of its task such as VP-236. Only provided fields change. A claim "
        "written here also lands on the task. Pass expectedUpdatedAt to refuse a stale write. "
        "Echoes a compact record unless view says otherwise.",
        annotations=IDEMPOTENT,
    )
    def update_board_card(
        id: CardId,
        column: BoardColumn | None = None,
        branchName: str | None = None,  # noqa: N803
        details: str | None = None,
        repositoryLocalPath: str | None = None,  # noqa: N803
        repositoryRemoteUrl: str | None = None,  # noqa: N803
        assignee: Assignee = None,
        expectedUpdatedAt: ExpectedUpdatedAt = None,  # noqa: N803
        view: ViewArg = None,
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {
            "column": column,
            "details": details,
            "assignee": assignee,
            "expected_updated_at": expectedUpdatedAt,
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
        return run(lambda s, a: echo_card(s, board.update_card(s, a, id, **changes), view))

    @mcp.tool(
        title="Set Card Readiness",
        description="Record an LLM-evaluated readiness score (1-10) with a short reason on a "
        "board card. Does not change updated_at, so a later content edit marks the score "
        "stale.",
        annotations=IDEMPOTENT,
    )
    def set_card_readiness(
        id: CardId,
        score: Score,
        reason: Annotated[str, Field(min_length=1)],
        view: ViewArg = None,
    ) -> dict[str, Any]:
        return run(
            lambda s, a: echo_card(s, readiness.set_card_readiness(s, a, id, score, reason), view)
        )

    @mcp.tool(
        title="Set Idea Readiness",
        description="Record an LLM-evaluated readiness score (1-10) with a short reason on an "
        "idea; mirrors onto its linked board card. Does not change updated_at, so a later "
        "content edit marks the score stale.",
        annotations=IDEMPOTENT,
    )
    def set_idea_readiness(
        id: TaskId,
        score: Score,
        reason: Annotated[str, Field(min_length=1)],
        view: ViewArg = None,
    ) -> dict[str, Any]:
        return run(
            lambda s, a: echo_idea(s, readiness.set_idea_readiness(s, a, id, score, reason), view)
        )

    @mcp.tool(
        title="List Idea Readiness History",
        description="List an idea's readiness rating history, newest first (score, reason, date).",
        annotations=READ_ONLY,
    )
    def list_idea_readiness(id: TaskId) -> dict[str, Any]:
        return run(lambda s, a: {"items": readiness.list_idea_readiness(s, a, id)})

    @mcp.tool(
        title="List Readiness",
        description="Latest readiness score per task for a whole project, or for named tasks. "
        "Accepts project keys and task keys such as VP-236. Set latestOnly to false for the "
        "full history.",
        annotations=READ_ONLY,
    )
    def list_readiness(
        project: ProjectFilter = None,
        tasks: list[Annotated[str, Field(min_length=1)]] | None = None,
        latestOnly: bool | None = None,  # noqa: N803
    ) -> dict[str, Any]:
        return run(
            lambda s, a: {
                "items": readiness.list_readiness(s, a, project, tasks, latestOnly is not False)
            }
        )

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
        description="List execution plans and other planning documents. Accepts a project id, "
        "key or title, and filters by kind or by modification time. Returns compact records "
        "unless view says otherwise.",
        annotations=READ_ONLY,
    )
    def list_documents(
        projectId: ProjectFilter = None,  # noqa: N803
        kind: list[DocumentKind] | None = None,
        updatedSince: UpdatedSince = None,  # noqa: N803
        view: ViewArg = None,
    ) -> dict[str, Any]:
        filters = documents.DocumentFilter(
            project=projectId, kind=kind or [], updated_since=updatedSince
        )
        return run(
            lambda s, a: {
                "items": project_documents(
                    documents.list_documents(s, a, filters=filters), view or View.COMPACT
                )
            }
        )

    return mcp
