"""Query parameters shared by the list endpoints. List parameters may be repeated or
comma-separated (`?assignee=a&assignee=b` or `?assignee=a,b`)."""

from enum import StrEnum
from typing import Annotated

from fastapi import Query

from vibepod_board.errors import BadRequest
from vibepod_board.services.views import View

ProjectFilter = Annotated[str | None, Query(alias="projectId")]
UpdatedSince = Annotated[
    str | None,
    Query(alias="updatedSince", description="Exclusive ISO timestamp with a UTC offset."),
]
Assignee = Annotated[list[str] | None, Query(description="Holders to match exactly.")]
Unassigned = Annotated[bool, Query(description="Also match work nobody holds; widens `assignee`.")]
Limit = Annotated[int | None, Query(gt=0)]
Cursor = Annotated[str | None, Query()]
# REST keeps returning full records unless a caller opts into a smaller view.
ViewParam = Annotated[View, Query()]


def project_filter(value: str | None) -> str | None:
    return value.strip() or None if value else None


def split_list(values: list[str] | None) -> list[str]:
    return [entry.strip() for value in values or [] for entry in value.split(",") if entry.strip()]


def enum_list[E: StrEnum](enum: type[E], values: list[str] | None, name: str) -> list[E]:
    allowed = [member.value for member in enum]
    parsed = []
    for value in split_list(values):
        if value not in allowed:
            raise BadRequest(f"Invalid {name}: {value}; expected one of {', '.join(allowed)}")
        parsed.append(enum(value))
    return parsed
