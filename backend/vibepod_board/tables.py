"""SQLModel table models. They mirror the schema the TypeScript server created, column for
column, so databases created by either backend are interchangeable."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
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
    # Set while automation is paused: claims are refused and workers are told to pause.
    automation_paused_at: datetime | None = _timestamp(nullable=True)
    automation_paused_reason: str | None = _text()
    created_at: datetime = _timestamp()
    updated_at: datetime = _timestamp()


class IdeaRow(SQLModel, table=True):
    __tablename__ = "ideas"
    __table_args__ = (
        UniqueConstraint("project_id", "task_number", name="ideas_project_id_task_number_key"),
        Index("ideas_project_updated_idx", "project_id", text("updated_at DESC")),
        Index("ideas_project_status_updated_idx", "project_id", "status", text("updated_at DESC")),
        Index(
            "ideas_project_updated_id_idx", "project_id", text("updated_at DESC"), text("id DESC")
        ),
        Index("ideas_project_assignee_idx", "project_id", "assignee"),
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
    # Free text naming whoever holds the task; the task owns it and its card mirrors it.
    assignee: str | None = _text()
    readiness_score: int | None = Field(default=None, sa_column=Column(Integer))
    readiness_reason: str | None = _text()
    readiness_evaluated_at: datetime | None = _timestamp(nullable=True)
    created_at: datetime = _timestamp()
    updated_at: datetime = _timestamp()


class BoardCardRow(SQLModel, table=True):
    __tablename__ = "board_cards"
    __table_args__ = (
        Index("board_cards_project_updated_idx", "project_id", text("updated_at DESC")),
        Index("board_cards_idea_idx", "idea_id"),
        Index(
            "board_cards_project_column_updated_idx",
            "project_id",
            "column_name",
            text("updated_at DESC"),
        ),
        Index(
            "board_cards_project_updated_id_idx",
            "project_id",
            text("updated_at DESC"),
            text("id DESC"),
        ),
        Index("board_cards_project_assignee_idx", "project_id", "assignee"),
        Index(
            "board_cards_claim_expires_idx",
            "claim_expires_at",
            postgresql_where=text("claim_expires_at IS NOT NULL"),
        ),
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
    github_pr_url: str | None = _text()
    github_pr_number: int | None = Field(default=None, sa_column=Column(Integer))
    github_pr_repository: str | None = _text()
    github_pr_state: str | None = _text()
    github_pr_draft: bool | None = Field(default=None, sa_column=Column(Boolean))
    github_pr_base: str | None = _text()
    github_pr_synced_at: datetime | None = _timestamp(nullable=True)
    repository_local_path: str | None = _text()
    repository_remote_url: str | None = _text()
    assignee: str | None = _text()
    labels: list[str] = _json_list()
    readiness_score: int | None = Field(default=None, sa_column=Column(Integer))
    readiness_reason: str | None = _text()
    readiness_evaluated_at: datetime | None = _timestamp(nullable=True)
    # Set when a done card leaves the board; the card and its task are kept.
    archived_at: datetime | None = _timestamp(nullable=True)
    # An automated claim: a lease the holder (the assignee) keeps renewing. It lapses at
    # `claim_expires_at`, and the task returns to Planned.
    claimed_at: datetime | None = _timestamp(nullable=True)
    claim_expires_at: datetime | None = _timestamp(nullable=True)
    # Failed automated attempts since the card was last put in Planned by hand.
    attempts: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    # A blocked card stays in Planned but is skipped by claims until it is moved to Planned
    # again.
    blocked_at: datetime | None = _timestamp(nullable=True)
    blocked_reason: str | None = _text()
    # The agent's question while the task waits for an answer; it is blocked meanwhile.
    question: str | None = _text()
    created_at: datetime = _timestamp()
    updated_at: datetime = _timestamp()


class TaskEventRow(SQLModel, table=True):
    """What happened to a task under automation: claims, hand-overs, failures, blocks."""

    __tablename__ = "task_events"
    __table_args__ = (Index("task_events_idea_idx", "idea_id", text("created_at DESC")),)

    id: str = Field(sa_column=Column(Text, primary_key=True))
    idea_id: str = _foreign_key("ideas.id", "CASCADE")
    kind: str = Field(sa_column=Column(Text, nullable=False))
    actor: str | None = _text()
    message: str = _text("")
    created_at: datetime = _timestamp()


class WorkerRow(SQLModel, table=True):
    """An automated runner connected to a project. Its name is the holder of the claims it
    takes; heartbeats keep it online and renew those claims."""

    __tablename__ = "workers"
    __table_args__ = (Index("workers_project_seen_idx", "project_id", text("last_seen_at DESC")),)

    id: str = Field(sa_column=Column(Text, primary_key=True))
    project_id: str = _foreign_key("projects.id", "CASCADE")
    name: str = Field(sa_column=Column(Text, nullable=False))
    agent: str = _text("")
    machine: str = _text("")
    # idle, working or paused, as last reported; offline is derived from `last_seen_at`.
    status: str = _text("idle")
    status_reason: str | None = _text()
    # The task being worked on, the step it is in and since when it is worked on.
    idea_id: str | None = _foreign_key("ideas.id", "SET NULL", nullable=True)
    step: str | None = _text()
    task_started_at: datetime | None = _timestamp(nullable=True)
    started_at: datetime = _timestamp()
    last_seen_at: datetime = _timestamp()
    stopped_at: datetime | None = _timestamp(nullable=True)
    # Asked from the board to stop; the worker learns it with its next heartbeat.
    stop_requested_at: datetime | None = _timestamp(nullable=True)


class TaskRunRow(SQLModel, table=True):
    """A report of one automated run of a task: what the agent did and how it ended."""

    __tablename__ = "task_runs"
    __table_args__ = (Index("task_runs_idea_idx", "idea_id", text("created_at DESC")),)

    id: str = Field(sa_column=Column(Text, primary_key=True))
    idea_id: str = _foreign_key("ideas.id", "CASCADE")
    worker_id: str | None = _foreign_key("workers.id", "SET NULL", nullable=True)
    # Kept by value: workers are pruned, their reports are not.
    worker_name: str | None = _text()
    agent: str | None = _text()
    outcome: str = Field(sa_column=Column(Text, nullable=False))
    summary: str = _text("")
    # [{"sha": ..., "subject": ...}] for the commits the run made.
    commits: list[dict[str, str]] = _json_list()
    branch_name: str | None = _text()
    verify_command: str | None = _text()
    verify_exit_code: int | None = Field(default=None, sa_column=Column(Integer))
    verify_output: str | None = _text()
    verify_output_truncated: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    duration_seconds: int | None = Field(default=None, sa_column=Column(Integer))
    failure_reason: str | None = _text()
    started_at: datetime | None = _timestamp(nullable=True)
    finished_at: datetime | None = _timestamp(nullable=True)
    created_at: datetime = _timestamp()


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
