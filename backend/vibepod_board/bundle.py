"""Project export/import bundle (versions 1 to 4).

Port of `src/shared/projectBundle.ts`: strict shapes (unknown fields are rejected) plus the
relationship rules that keep a bundle self-contained. The client validates with the TS copy
before upload; the server validates again because the file is untrusted.
"""

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from vibepod_board.enums import BoardColumn, DocumentKind, IdeaStatus
from vibepod_board.graph import find_cyclic_task_ids, normalize_ids
from vibepod_board.schemas import ApiModel, Timestamp


def _iso_string(value: Any) -> Any:
    if not isinstance(value, str):
        raise ValueError("Expected an ISO 8601 timestamp string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a UTC offset")
    return parsed


def _trimmed_title(value: str) -> str:
    if not value.strip():
        raise ValueError("Title is required")
    return value.strip()


BundleTimestamp = Annotated[Timestamp, BeforeValidator(_iso_string)]
Id = Annotated[StrictStr, Field(min_length=1)]
Title = Annotated[StrictStr, AfterValidator(_trimmed_title)]
Score = Annotated[StrictInt, Field(ge=1, le=10)]


class BundleModel(ApiModel):
    model_config = ConfigDict(extra="forbid")


class BundleProject(BundleModel):
    id: Id
    key: Annotated[StrictStr, Field(pattern=r"^[A-Z]{1,3}$")]
    title: Title
    summary: StrictStr
    created_at: BundleTimestamp
    updated_at: BundleTimestamp


class BundleIdea(BundleModel):
    id: Id
    project_id: Id
    task_number: Annotated[StrictInt, Field(gt=0)]
    title: Title
    summary: StrictStr
    details: StrictStr
    status: IdeaStatus
    labels: list[StrictStr]
    acceptance_criteria: list[StrictStr]
    depends_on: list[Id]
    # Derived from `depends_on` and recomputed on import, so a bundle may omit them.
    blocks: list[Id] = Field(default_factory=list)
    blocked_by: list[Id] = Field(default_factory=list)
    github_issue_url: StrictStr | None = None
    github_issue_number: StrictInt | None = None
    github_repository: StrictStr | None = None
    github_issue_state: Literal["open", "closed"] | None = None
    github_issue_updated_at: BundleTimestamp | None = None
    github_synced_at: BundleTimestamp | None = None
    repository_local_path: StrictStr | None = None
    repository_remote_url: StrictStr | None = None
    assignee: StrictStr | None = None
    readiness_score: Score | None = None
    readiness_reason: StrictStr | None = None
    readiness_evaluated_at: BundleTimestamp | None = None
    created_at: BundleTimestamp
    updated_at: BundleTimestamp


class BundleBoardCard(BundleModel):
    id: Id
    project_id: Id
    title: Title
    details: StrictStr
    column: BoardColumn
    branch_name: StrictStr | None = None
    idea_id: StrictStr | None = None
    github_issue_url: StrictStr | None = None
    github_issue_number: StrictInt | None = None
    github_pr_url: StrictStr | None = None
    github_pr_number: StrictInt | None = None
    github_pr_repository: StrictStr | None = None
    github_pr_state: Literal["open", "closed", "merged"] | None = None
    github_pr_draft: StrictBool | None = None
    github_pr_base: StrictStr | None = None
    github_pr_synced_at: BundleTimestamp | None = None
    repository_local_path: StrictStr | None = None
    repository_remote_url: StrictStr | None = None
    assignee: StrictStr | None = None
    labels: list[StrictStr]
    # A card mirrors the dependency state of its task, so both fields are derived.
    depends_on: list[Id] = Field(default_factory=list)
    blocked_by: list[Id] = Field(default_factory=list)
    readiness_score: Score | None = None
    readiness_reason: StrictStr | None = None
    readiness_evaluated_at: BundleTimestamp | None = None
    archived_at: BundleTimestamp | None = None
    created_at: BundleTimestamp
    updated_at: BundleTimestamp


class BundleReadinessEvent(BundleModel):
    id: Id
    idea_id: Id
    score: Score
    reason: StrictStr
    created_at: BundleTimestamp


class BundleDocument(BundleModel):
    id: Id
    project_id: Id
    title: Title
    kind: DocumentKind
    content: StrictStr
    linked_idea_ids: list[Id]
    linked_card_ids: list[Id]
    created_at: BundleTimestamp
    updated_at: BundleTimestamp


# Fields added by bundle version 4: a card's linked pull request.
PULL_REQUEST_FIELDS = (
    "github_pr_url",
    "github_pr_number",
    "github_pr_repository",
    "github_pr_state",
    "github_pr_draft",
    "github_pr_base",
    "github_pr_synced_at",
)
GITHUB_SYNC_FIELDS = (
    "github_repository",
    "github_issue_state",
    "github_issue_updated_at",
    "github_synced_at",
)


def _duplicates[T](values: Iterable[T]) -> list[T]:
    return [value for value, count in Counter(values).items() if count > 1]


class ProjectBundle(BundleModel):
    # 2 adds GitHub sync state on tasks, 3 archived board cards, 4 linked pull requests on
    # board cards; older bundles are still accepted.
    bundle_version: Literal[1, 2, 3, 4]
    exported_at: BundleTimestamp
    project: BundleProject
    ideas: list[BundleIdea]
    board_cards: list[BundleBoardCard]
    readiness_events: list[BundleReadinessEvent]
    documents: list[BundleDocument]

    @model_validator(mode="after")
    def _validate_relationships(self) -> Self:
        issues = relationship_issues(self)
        if issues:
            raise ValueError("; ".join(issues))
        return self


def relationship_issues(bundle: ProjectBundle) -> list[str]:
    project_id = bundle.project.id
    idea_ids = {idea.id for idea in bundle.ideas}
    card_ids = {card.id for card in bundle.board_cards}
    issues: list[str] = []

    issues += [f"Duplicate idea id: {i}" for i in _duplicates(i.id for i in bundle.ideas)]
    issues += [
        f"Duplicate task number: {n}"
        for n in _duplicates(idea.task_number for idea in bundle.ideas)
    ]
    linked = [
        f"{idea.github_repository.lower()}#{idea.github_issue_number}"
        for idea in bundle.ideas
        if idea.github_repository and idea.github_issue_number
    ]
    issues += [f"Duplicate GitHub issue link: {issue}" for issue in _duplicates(linked)]
    if bundle.bundle_version < 3:
        issues += [
            "Archived board cards require bundleVersion 3"
            for card in bundle.board_cards
            if card.archived_at is not None
        ][:1]
    issues += [
        f"Archived board card must be in done: {card.id}"
        for card in bundle.board_cards
        if card.archived_at is not None and card.column != BoardColumn.DONE
    ]
    if bundle.bundle_version < 4:
        issues += [
            "Linked pull requests require bundleVersion 4"
            for card in bundle.board_cards
            if any(getattr(card, field) is not None for field in PULL_REQUEST_FIELDS)
        ][:1]
    if bundle.bundle_version == 1:
        issues += [
            "GitHub sync fields require bundleVersion 2"
            for idea in bundle.ideas
            if any(getattr(idea, field) is not None for field in GITHUB_SYNC_FIELDS)
        ][:1]
    issues += [
        f"Duplicate board card id: {i}" for i in _duplicates(c.id for c in bundle.board_cards)
    ]
    issues += [
        f"Duplicate readiness event id: {i}"
        for i in _duplicates(e.id for e in bundle.readiness_events)
    ]
    issues += [f"Duplicate document id: {i}" for i in _duplicates(d.id for d in bundle.documents)]

    for idea in bundle.ideas:
        if idea.project_id != project_id:
            issues.append("Idea belongs to another project")
        issues += [
            f"Dependency is not in bundle: {dependency}"
            for dependency in idea.depends_on
            if dependency not in idea_ids
        ]

    # Same rule the store applies when it orders work, so a bundle can never validate
    # here and then fail to order after import.
    edges = {idea.id: normalize_ids(idea.depends_on) for idea in bundle.ideas}
    issues += [f"Dependency cycle includes: {i}" for i in find_cyclic_task_ids(edges)]

    for card in bundle.board_cards:
        if card.project_id != project_id:
            issues.append("Board card belongs to another project")
        if card.idea_id and card.idea_id not in idea_ids:
            issues.append(f"Linked idea is not in bundle: {card.idea_id}")

    issues += [
        f"Readiness idea is not in bundle: {event.idea_id}"
        for event in bundle.readiness_events
        if event.idea_id not in idea_ids
    ]

    for document in bundle.documents:
        if document.project_id != project_id:
            issues.append("Document belongs to another project")
        issues += [
            f"Linked idea is not in bundle: {i}"
            for i in document.linked_idea_ids
            if i not in idea_ids
        ]
        issues += [
            f"Linked card is not in bundle: {i}"
            for i in document.linked_card_ids
            if i not in card_ids
        ]
    return issues


def parse_project_bundle(data: Any) -> ProjectBundle:
    return ProjectBundle.model_validate(data)


class ImportProjectRequest(BundleModel):
    bundle: ProjectBundle
    replace_existing: StrictBool = False
