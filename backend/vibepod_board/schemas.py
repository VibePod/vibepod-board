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

from vibepod_board.enums import BoardColumn, DocumentKind, IdeaStatus


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
    labels: list[str]
    depends_on: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    readiness_score: int | None = None
    readiness_reason: str | None = None
    readiness_evaluated_at: Timestamp | None = None
    archived_at: Timestamp | None = None
    created_at: Timestamp
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
