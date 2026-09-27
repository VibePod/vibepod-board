"""Request bodies. They follow the zod schemas of the TS server: unknown keys are ignored,
titles are trimmed, and numbers and booleans must be real JSON numbers and booleans."""

import re
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator, Field, StrictBool, StrictInt, StrictStr

from vibepod_board.enums import BoardColumn, DocumentKind, IdeaStatus
from vibepod_board.schemas import ApiModel


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


class IdeaUpdate(ApiModel):
    title: RequiredText | None = None
    summary: StrictStr | None = None
    details: StrictStr | None = None
    labels: list[StrictStr] | None = None
    acceptance_criteria: list[StrictStr] | None = None
    depends_on: list[StrictStr] | None = None
    repository_local_path: StrictStr | None = None
    repository_remote_url: StrictStr | None = None
    status: Status | None = None


class DependencyAdd(ApiModel):
    depends_on_id: RequiredText


class DependencySet(ApiModel):
    depends_on_ids: Ids


class ReadyRequest(ApiModel):
    available: StrictBool = True


class BoardCardUpdate(ApiModel):
    column: BoardColumn | None = None
    branch_name: StrictStr | None = None
    details: StrictStr | None = None
    repository_local_path: StrictStr | None = None
    repository_remote_url: StrictStr | None = None


class ReadinessRequest(ApiModel):
    score: Annotated[StrictInt, Field(ge=1, le=10)]
    reason: RequiredText


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
