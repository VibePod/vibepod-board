"""API models. They serialize to the exact JSON the TypeScript server produced: camelCase
keys, optional fields left out rather than sent as null, and millisecond UTC timestamps."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    SerializerFunctionWrapHandler,
    model_serializer,
)
from pydantic.alias_generators import to_camel

from vibepod_board.enums import (
    BoardColumn,
    DocumentKind,
    IdeaStatus,
    InstructionType,
    RunOutcome,
    TaskEventKind,
    WorkerState,
    WorkerStep,
)


def format_timestamp(value: datetime) -> str:
    """`2026-08-14T12:00:00.000Z`, the shape of JavaScript's `toISOString()`."""
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now() -> datetime:
    """Truncated to milliseconds, so stored and echoed values round-trip exactly."""
    current = datetime.now(UTC)
    return current.replace(microsecond=current.microsecond // 1000 * 1000)


Timestamp = Annotated[datetime, PlainSerializer(format_timestamp, return_type=str)]


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        use_enum_values=True,
    )

    @model_serializer(mode="wrap")
    def _drop_unset_optionals(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


class Project(ApiModel):
    id: str
    key: str
    title: str
    summary: str
    created_at: Timestamp
    updated_at: Timestamp


class ProjectRef(ApiModel):
    id: str
    key: str
    title: str


class Idea(ApiModel):
    id: str
    project_id: str
    task_number: int
    title: str
    summary: str
    details: str
    status: IdeaStatus
    labels: list[str]
    acceptance_criteria: list[str]
    depends_on: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    github_issue_url: str | None = None
    github_issue_number: int | None = None
    github_repository: str | None = None
    github_issue_state: str | None = None
    github_issue_updated_at: Timestamp | None = None
    github_synced_at: Timestamp | None = None
    repository_local_path: str | None = None
    repository_remote_url: str | None = None
    assignee: str | None = None
    readiness_score: int | None = None
    readiness_reason: str | None = None
    readiness_evaluated_at: Timestamp | None = None
    created_at: Timestamp
    updated_at: Timestamp


class BoardCard(ApiModel):
    id: str
    project_id: str
    title: str
    details: str
    column: BoardColumn
    branch_name: str | None = None
    idea_id: str | None = None
    github_issue_url: str | None = None
    github_issue_number: int | None = None
    repository_local_path: str | None = None
    repository_remote_url: str | None = None
    assignee: str | None = None
    labels: list[str]
    depends_on: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    readiness_score: int | None = None
    readiness_reason: str | None = None
    readiness_evaluated_at: Timestamp | None = None
    archived_at: Timestamp | None = None
    # An automated claim, held by the assignee until it expires or is handed back.
    claimed_at: Timestamp | None = None
    claim_expires_at: Timestamp | None = None
    # Failed automated attempts since the card was last put in Planned by hand.
    attempts: int = 0
    # A blocked card stays in Planned and is skipped by claims.
    blocked_at: Timestamp | None = None
    blocked_reason: str | None = None
    # The agent's question while the task waits for an answer.
    question: str | None = None
    created_at: Timestamp
    updated_at: Timestamp


class KeyedIdea(Idea):
    """A task on its way out, carrying its human key. The key is never stored: the project
    bundle rejects unknown properties on a task."""

    key: str | None = None


class KeyedBoardCard(BoardCard):
    key: str | None = None


class EntityRef(ApiModel):
    id: str
    key: str | None = None
    updated_at: Timestamp


class CompactIdea(ApiModel):
    """Identity, state and relationships. Dependencies are named by key, and the free text
    is reduced to its size."""

    id: str
    key: str | None = None
    project_id: str
    task_number: int
    title: str
    status: IdeaStatus
    labels: list[str]
    column: BoardColumn | None = None
    assignee: str | None = None
    readiness_score: int | None = None
    depends_on: list[str]
    blocked_by: list[str]
    details_length: int
    acceptance_criteria_count: int
    updated_at: Timestamp


class CompactBoardCard(ApiModel):
    id: str
    key: str | None = None
    idea_id: str | None = None
    project_id: str
    title: str
    column: BoardColumn
    branch_name: str | None = None
    labels: list[str]
    blocked_by: list[str]
    assignee: str | None = None
    claimed_at: Timestamp | None = None
    # Left out while zero.
    attempts: int | None = None
    blocked_reason: str | None = None
    question: str | None = None
    readiness_score: int | None = None
    details_length: int
    updated_at: Timestamp


class CompactDocument(ApiModel):
    id: str
    project_id: str
    title: str
    kind: DocumentKind
    linked_idea_ids: list[str]
    content_length: int
    updated_at: Timestamp


class DeletedIdea(ApiModel):
    id: str
    task_id: str
    # Tasks that depended on the deleted one and lost that dependency.
    dependents: list[str]


class ReadinessEvent(ApiModel):
    id: str
    idea_id: str
    score: int
    reason: str
    created_at: Timestamp


class PlanDocument(ApiModel):
    id: str
    project_id: str
    title: str
    kind: DocumentKind
    content: str
    linked_idea_ids: list[str]
    linked_card_ids: list[str]
    created_at: Timestamp
    updated_at: Timestamp


class ActivityEvent(ApiModel):
    id: str
    type: str
    message: str
    created_at: Timestamp


class TaskEvent(ApiModel):
    id: str
    idea_id: str
    kind: TaskEventKind
    # Who did it: the claim holder for runner calls, else the admin or the token's name.
    actor: str | None = None
    message: str
    created_at: Timestamp


class Claim(ApiModel):
    task: KeyedIdea
    card: KeyedBoardCard


class ClaimResult(ApiModel):
    claimed: bool
    # The claimed task and its card; left out when nothing was claimed.
    item: Claim | None = None
    # Why nothing was claimed.
    reason: str | None = None
    # True when nothing was claimed because automation of the project is paused.
    paused: bool | None = None


class ApiTokenSummary(ApiModel):
    id: str
    name: str
    projects: list[ProjectRef]
    created_at: Timestamp
    last_used_at: Timestamp | None = None
    revoked_at: Timestamp | None = None


class BoardColumns(ApiModel):
    ready: list[BoardCard] = Field(default_factory=list)
    planned: list[BoardCard] = Field(default_factory=list)
    in_progress: list[BoardCard] = Field(default_factory=list)
    review: list[BoardCard] = Field(default_factory=list)
    pr_ready: list[BoardCard] = Field(default_factory=list)
    done: list[BoardCard] = Field(default_factory=list)

    # Column names are snake_case on the wire too, so they are exempt from the camelCase alias.
    model_config = ConfigDict(alias_generator=None)


class WorkOrderItem(ApiModel):
    id: str
    task_id: str
    project_id: str
    title: str
    status: IdeaStatus
    column: BoardColumn | None = None
    card_id: str | None = None
    position: int
    wave: int
    depends_on: list[str]
    blocked_by: list[str]
    is_blocked: bool
    is_complete: bool
    is_actionable: bool


class TaskWorkOrder(ApiModel):
    items: list[WorkOrderItem]
    cyclic_task_ids: list[str]


class BoardState(ApiModel):
    schema_version: Literal[3] = 3
    projects: list[Project]
    ideas: list[Idea]
    board_cards: list[BoardCard]
    readiness_events: list[ReadinessEvent]
    documents: list[PlanDocument]
    activity: list[ActivityEvent]


class CreatedApiToken(ApiModel):
    item: ApiTokenSummary
    token: str


class ImportProjectResult(ApiModel):
    item: Project
    replaced: bool


class Worker(ApiModel):
    id: str
    project_id: str
    name: str
    agent: str
    machine: str
    # offline once the heartbeats stopped or the worker signed off.
    status: WorkerState
    status_reason: str | None = None
    # The task being worked on, with its key and title, the step it is in and since when.
    task_id: str | None = None
    task_key: str | None = None
    task_title: str | None = None
    step: WorkerStep | None = None
    task_started_at: Timestamp | None = None
    started_at: Timestamp
    last_seen_at: Timestamp
    stopped_at: Timestamp | None = None
    # Asked from the board to stop; cleared once the worker signed off.
    stop_requested_at: Timestamp | None = None


class WorkerInstruction(ApiModel):
    """Something the board asks a worker to do, delivered with the heartbeat reply."""

    type: InstructionType
    reason: str | None = None
    task_id: str | None = None
    task_key: str | None = None


class WorkerSession(ApiModel):
    """The reply to a registration or heartbeat."""

    item: Worker
    instructions: list[WorkerInstruction] = Field(default_factory=list)
    # How often the board expects a heartbeat.
    heartbeat_seconds: int


class AutomationState(ApiModel):
    project_id: str
    paused: bool
    paused_at: Timestamp | None = None
    reason: str | None = None


class WorkerList(ApiModel):
    items: list[Worker]
    # The automation state when the list is for one project.
    automation: AutomationState | None = None


class RunCommit(ApiModel):
    sha: str
    subject: str = ""


class TaskRun(ApiModel):
    """The report of one automated run of a task."""

    id: str
    idea_id: str
    worker_id: str | None = None
    worker_name: str | None = None
    agent: str | None = None
    outcome: RunOutcome
    summary: str
    commits: list[RunCommit]
    branch_name: str | None = None
    verify_command: str | None = None
    verify_exit_code: int | None = None
    verify_output: str | None = None
    # True when the verify output was cut down to fit the report.
    verify_output_truncated: bool = False
    duration_seconds: int | None = None
    failure_reason: str | None = None
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None
    created_at: Timestamp
