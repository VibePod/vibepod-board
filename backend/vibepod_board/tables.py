"""SQLModel table models. They mirror the schema the TypeScript server created, column for
column, so databases created by either backend are interchangeable."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _timestamp(nullable: bool = False) -> Any:
    return Field(
        default=None if nullable else ...,
        sa_column=Column(DateTime(timezone=True), nullable=nullable),
    )


def _text(default: str | None = None) -> Any:
    """A `text not null default ''` column when a default is given, else nullable text."""
    if default is None:
        return Field(default=None, sa_column=Column(Text, nullable=True))
    return Field(
        default=default,
        sa_column=Column(Text, nullable=False, server_default=text(f"'{default}'")),
    )


def _json_list() -> Any:
    return Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )


def _foreign_key(target: str, ondelete: str, nullable: bool = False) -> Any:
    return Field(
        default=None if nullable else ...,
        sa_column=Column(Text, ForeignKey(target, ondelete=ondelete), nullable=nullable),
    )


class ProjectRow(SQLModel, table=True):
    __tablename__ = "projects"

    id: str = Field(sa_column=Column(Text, primary_key=True))
    key: str = Field(sa_column=Column(Text, nullable=False, unique=True))
    title: str = Field(sa_column=Column(Text, nullable=False))
    summary: str = _text("")
    # Highest task number ever issued in the project; numbers are never reused.
    last_task_number: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    created_at: datetime = _timestamp()
    updated_at: datetime = _timestamp()


class IdeaRow(SQLModel, table=True):
    __tablename__ = "ideas"
    __table_args__ = (
        UniqueConstraint("project_id", "task_number", name="ideas_project_id_task_number_key"),
        Index("ideas_project_updated_idx", "project_id", text("updated_at DESC")),
        Index(
            "ideas_github_issue_key",
            "project_id",
            "github_repository",
            "github_issue_number",
            unique=True,
            postgresql_where=text(
                "github_repository IS NOT NULL AND github_issue_number IS NOT NULL"
            ),
        ),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    project_id: str = _foreign_key("projects.id", "CASCADE")
    task_number: int = Field(sa_column=Column(Integer, nullable=False))
    title: str = Field(sa_column=Column(Text, nullable=False))
    summary: str = _text("")
    details: str = _text("")
    status: str = Field(sa_column=Column(Text, nullable=False))
    labels: list[str] = _json_list()
    acceptance_criteria: list[str] = _json_list()
    github_issue_url: str | None = _text()
    github_issue_number: int | None = Field(default=None, sa_column=Column(Integer))
    github_repository: str | None = _text()
    github_issue_state: str | None = _text()
    github_issue_updated_at: datetime | None = _timestamp(nullable=True)
    github_synced_at: datetime | None = _timestamp(nullable=True)
    repository_local_path: str | None = _text()
    repository_remote_url: str | None = _text()
    readiness_score: int | None = Field(default=None, sa_column=Column(Integer))
    readiness_reason: str | None = _text()
    readiness_evaluated_at: datetime | None = _timestamp(nullable=True)
    created_at: datetime = _timestamp()
    updated_at: datetime = _timestamp()


class BoardCardRow(SQLModel, table=True):
    __tablename__ = "board_cards"
    __table_args__ = (
        Index("board_cards_project_updated_idx", "project_id", text("updated_at DESC")),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    project_id: str = _foreign_key("projects.id", "CASCADE")
    idea_id: str | None = _foreign_key("ideas.id", "SET NULL", nullable=True)
    title: str = Field(sa_column=Column(Text, nullable=False))
    details: str = _text("")
    column_name: str = Field(sa_column=Column(Text, nullable=False))
    branch_name: str | None = _text()
    github_issue_url: str | None = _text()
    github_issue_number: int | None = Field(default=None, sa_column=Column(Integer))
    repository_local_path: str | None = _text()
    repository_remote_url: str | None = _text()
    labels: list[str] = _json_list()
    readiness_score: int | None = Field(default=None, sa_column=Column(Integer))
    readiness_reason: str | None = _text()
    readiness_evaluated_at: datetime | None = _timestamp(nullable=True)
    # Set when a done card leaves the board; the card and its task are kept.
    archived_at: datetime | None = _timestamp(nullable=True)
    created_at: datetime = _timestamp()
    updated_at: datetime = _timestamp()


class DocumentRow(SQLModel, table=True):
    __tablename__ = "documents"
    __table_args__ = (
        Index("documents_project_updated_idx", "project_id", text("updated_at DESC")),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    project_id: str = _foreign_key("projects.id", "CASCADE")
    title: str = Field(sa_column=Column(Text, nullable=False))
    kind: str = Field(sa_column=Column(Text, nullable=False))
    content: str = _text("")
    linked_idea_ids: list[str] = _json_list()
    linked_card_ids: list[str] = _json_list()
    created_at: datetime = _timestamp()
    updated_at: datetime = _timestamp()


class ActivityEventRow(SQLModel, table=True):
    __tablename__ = "activity_events"
    __table_args__ = (Index("activity_events_created_idx", text("created_at DESC")),)

    id: str = Field(sa_column=Column(Text, primary_key=True))
    type: str = Field(sa_column=Column(Text, nullable=False))
    message: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = _timestamp()


class ApiTokenRow(SQLModel, table=True):
    __tablename__ = "api_tokens"

    id: str = Field(sa_column=Column(Text, primary_key=True))
    name: str = Field(sa_column=Column(Text, nullable=False))
    token_hash: str = Field(sa_column=Column(Text, nullable=False, unique=True))
    created_at: datetime = _timestamp()
    last_used_at: datetime | None = _timestamp(nullable=True)
    revoked_at: datetime | None = _timestamp(nullable=True)


class ApiTokenProjectRow(SQLModel, table=True):
    __tablename__ = "api_token_projects"

    token_id: str = Field(
        sa_column=Column(Text, ForeignKey("api_tokens.id", ondelete="CASCADE"), primary_key=True)
    )
    project_id: str = Field(
        sa_column=Column(Text, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    )


class TaskDependencyRow(SQLModel, table=True):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        CheckConstraint("idea_id <> depends_on_idea_id", name="task_dependencies_check"),
        Index("task_dependencies_depends_on_idx", "depends_on_idea_id"),
    )

    idea_id: str = Field(
        sa_column=Column(Text, ForeignKey("ideas.id", ondelete="CASCADE"), primary_key=True)
    )
    depends_on_idea_id: str = Field(
        sa_column=Column(Text, ForeignKey("ideas.id", ondelete="CASCADE"), primary_key=True)
    )
    created_at: datetime = _timestamp()


class IdeaReadinessEventRow(SQLModel, table=True):
    __tablename__ = "idea_readiness_events"
    __table_args__ = (Index("idea_readiness_events_idea_idx", "idea_id", text("created_at DESC")),)

    id: str = Field(sa_column=Column(Text, primary_key=True))
    idea_id: str = _foreign_key("ideas.id", "CASCADE")
    score: int = Field(sa_column=Column(Integer, nullable=False))
    reason: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = _timestamp()
