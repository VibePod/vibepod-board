"""Request bodies. They follow the zod schemas of the TS server: unknown keys are ignored,
titles are trimmed, and numbers and booleans must be real JSON numbers and booleans."""

import re
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BeforeValidator,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
)

from vibepod_board.enums import (
    BoardColumn,
    DocumentKind,
    IdeaStatus,
    ReleaseOutcome,
    RunOutcome,
    WorkerStatus,
    WorkerStep,
)
from vibepod_board.schemas import ApiModel
from vibepod_board.services.claims import MAX_LEASE_SECONDS, MIN_LEASE_SECONDS
from vibepod_board.services.listing import BATCH_LIMIT


def _required_trimmed(value: str) -> str:
    if not value.strip():
        raise ValueError("String must contain at least 1 character(s)")
    return value.strip()


def _project_key(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Z]{1,3}", value):
        raise ValueError("Project ID must be 1 to 3 capital letters")
    return value


def _legacy_status(value: Any) -> Any:
    # An early client misspelled the status; accept it on the way in.
    return "denied" if value == "dennied" else value


RequiredText = Annotated[StrictStr, AfterValidator(_required_trimmed)]
ProjectKey = Annotated[StrictStr, AfterValidator(_project_key)]
Status = Annotated[IdeaStatus, BeforeValidator(_legacy_status)]
Ids = list[RequiredText]


class LoginRequest(ApiModel):
    username: StrictStr
    password: StrictStr


class ProjectCreate(ApiModel):
    key: ProjectKey
    title: RequiredText
    summary: StrictStr = ""


class ProjectUpdate(ApiModel):
    key: ProjectKey | None = None
    title: RequiredText | None = None
    summary: StrictStr | None = None


class IdeaCreate(ApiModel):
    project_id: StrictStr | None = None
    title: RequiredText
    summary: StrictStr = ""
    details: StrictStr = ""
    labels: list[StrictStr] = Field(default_factory=list)
    acceptance_criteria: list[StrictStr] = Field(default_factory=list)
    depends_on: list[StrictStr] = Field(default_factory=list)
    repository_local_path: StrictStr | None = None
    repository_remote_url: StrictStr | None = None
    github_issue_url: StrictStr | None = None
    assignee: StrictStr | None = None


class ReadinessRequest(ApiModel):
    score: Annotated[StrictInt, Field(ge=1, le=10)]
    reason: RequiredText


class IdeaUpdate(ApiModel):
    title: RequiredText | None = None
    summary: StrictStr | None = None
    details: StrictStr | None = None
    labels: list[StrictStr] | None = None
    acceptance_criteria: list[StrictStr] | None = None
    depends_on: list[StrictStr] | None = None
    repository_local_path: StrictStr | None = None
    repository_remote_url: StrictStr | None = None
    github_issue_url: StrictStr | None = None
    # Free text naming whoever holds the task; an empty string releases it.
    assignee: StrictStr | None = None
    status: Status | None = None
    # True puts the task on the board, false removes its card.
    on_board: StrictBool | None = None
    # Records a readiness score in the same write.
    readiness: ReadinessRequest | None = None
    # Refuses the write (409) when the task changed since this updatedAt.
    expected_updated_at: RequiredText | None = None


class IdeaBatchItem(IdeaUpdate):
    id: RequiredText


class IdeaBatch(ApiModel):
    items: Annotated[list[IdeaBatchItem], Field(max_length=BATCH_LIMIT)]


class DependencyAdd(ApiModel):
    depends_on_id: RequiredText


class DependencySet(ApiModel):
    depends_on_ids: Ids


class ReadyRequest(ApiModel):
    available: StrictBool = True
    readiness: ReadinessRequest | None = None


class BoardCardUpdate(ApiModel):
    column: BoardColumn | None = None
    branch_name: StrictStr | None = None
    details: StrictStr | None = None
    repository_local_path: StrictStr | None = None
    repository_remote_url: StrictStr | None = None
    assignee: StrictStr | None = None
    expected_updated_at: RequiredText | None = None


class BoardCardBatchItem(BoardCardUpdate):
    id: RequiredText


class BoardCardBatch(ApiModel):
    items: Annotated[list[BoardCardBatchItem], Field(max_length=BATCH_LIMIT)]


class ArchiveDoneRequest(ApiModel):
    project_id: RequiredText


LeaseSeconds = Annotated[StrictInt, Field(ge=MIN_LEASE_SECONDS, le=MAX_LEASE_SECONDS)]


class ClaimRequest(ApiModel):
    project_id: RequiredText
    # Who claims: becomes the task's assignee, and must be named again to report back.
    assignee: RequiredText
    # Claim this task rather than the next one in the work order.
    task: RequiredText | None = None
    # Only tasks carrying every one of these labels (case-insensitive).
    labels: list[RequiredText] = Field(default_factory=list)
    # Only tasks whose latest readiness score is at least this.
    min_readiness: Annotated[StrictInt, Field(ge=1, le=10)] | None = None
    # Tasks to pass over, such as a run the runner just saw cancelled.
    exclude: Annotated[list[RequiredText], Field(max_length=BATCH_LIMIT)] = Field(
        default_factory=list
    )
    # How long the claim lasts unless renewed; the server default applies when omitted.
    lease_seconds: LeaseSeconds | None = None


class RenewClaimRequest(ApiModel):
    assignee: RequiredText
    lease_seconds: LeaseSeconds | None = None


class HandoverRequest(ApiModel):
    assignee: RequiredText
    branch_name: RequiredText | None = None
    note: StrictStr | None = None
    expected_updated_at: RequiredText | None = None


class ReleaseRequest(ApiModel):
    assignee: RequiredText
    outcome: ReleaseOutcome = ReleaseOutcome.FAILED
    # Why; required to block, since it is the reason shown on the card.
    note: StrictStr | None = None
    # Failed attempts before the task is blocked; the server default applies when omitted.
    max_attempts: Annotated[StrictInt, Field(ge=1, le=100)] | None = None


class DocumentCreate(ApiModel):
    project_id: StrictStr | None = None
    title: RequiredText
    kind: DocumentKind = DocumentKind.EXECUTION_PLAN
    content: StrictStr = ""
    linked_idea_ids: list[StrictStr] = Field(default_factory=list)
    linked_card_ids: list[StrictStr] = Field(default_factory=list)


class DocumentUpdate(ApiModel):
    title: RequiredText | None = None
    kind: DocumentKind | None = None
    content: StrictStr | None = None
    linked_idea_ids: list[StrictStr] | None = None
    linked_card_ids: list[StrictStr] | None = None


class TokenCreate(ApiModel):
    name: RequiredText
    project_ids: Annotated[Ids, Field(min_length=1)]


class TokenUpdate(ApiModel):
    name: RequiredText | None = None
    project_ids: Annotated[Ids, Field(min_length=1)] | None = None


class WorkerRegistration(ApiModel):
    project_id: RequiredText
    # Shown on the board and used as the holder of the claims the worker takes.
    name: RequiredText
    agent: StrictStr = ""
    machine: StrictStr = ""


class HeartbeatRequest(ApiModel):
    status: WorkerStatus
    # Why the worker is paused, such as a reached usage limit.
    status_reason: StrictStr | None = None
    # The task being worked on; required while working.
    task: RequiredText | None = None
    step: WorkerStep | None = None
    # The lease the worker's claims are renewed by; the server default when omitted.
    lease_seconds: LeaseSeconds | None = None


class PauseRequest(ApiModel):
    reason: StrictStr | None = None


class CancelRunRequest(ApiModel):
    reason: StrictStr | None = None
    # Refuses the cancel (409) when the card changed since this updatedAt.
    expected_updated_at: RequiredText | None = None


class RunCommitInput(ApiModel):
    sha: Annotated[StrictStr, Field(pattern=r"^[0-9a-fA-F]{4,64}$")]
    subject: StrictStr = ""


class RunReportRequest(ApiModel):
    outcome: RunOutcome
    # The agent's own summary of the run.
    summary: StrictStr = ""
    commits: Annotated[list[RunCommitInput], Field(max_length=1000)] = Field(default_factory=list)
    branch_name: StrictStr | None = None
    verify_command: StrictStr | None = None
    verify_exit_code: StrictInt | None = None
    # Cut down to its head and tail when long.
    verify_output: StrictStr | None = None
    duration_seconds: Annotated[StrictInt, Field(ge=0)] | None = None
    failure_reason: StrictStr | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    worker_id: RequiredText | None = None
    worker_name: StrictStr | None = None
    agent: StrictStr | None = None
