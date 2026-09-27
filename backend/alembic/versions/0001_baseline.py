"""Baseline: the schema the TypeScript server created.

Revision ID: 0001
Revises:
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)
EMPTY_TEXT = sa.text("''")
EMPTY_JSON = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=EMPTY_TEXT),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
    )
    op.create_table(
        "ideas",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Text(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=EMPTY_TEXT),
        sa.Column("details", sa.Text(), nullable=False, server_default=EMPTY_TEXT),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("labels", JSONB(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("acceptance_criteria", JSONB(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("github_issue_url", sa.Text()),
        sa.Column("github_issue_number", sa.Integer()),
        sa.Column("repository_local_path", sa.Text()),
        sa.Column("repository_remote_url", sa.Text()),
        sa.Column("readiness_score", sa.Integer()),
        sa.Column("readiness_reason", sa.Text()),
        sa.Column("readiness_evaluated_at", TIMESTAMP),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
        sa.UniqueConstraint("project_id", "task_number", name="ideas_project_id_task_number_key"),
    )
    op.create_table(
        "board_cards",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Text(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idea_id", sa.Text(), sa.ForeignKey("ideas.id", ondelete="SET NULL")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("details", sa.Text(), nullable=False, server_default=EMPTY_TEXT),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("branch_name", sa.Text()),
        sa.Column("github_issue_url", sa.Text()),
        sa.Column("github_issue_number", sa.Integer()),
        sa.Column("repository_local_path", sa.Text()),
        sa.Column("repository_remote_url", sa.Text()),
        sa.Column("labels", JSONB(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("readiness_score", sa.Integer()),
        sa.Column("readiness_reason", sa.Text()),
        sa.Column("readiness_evaluated_at", TIMESTAMP),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Text(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=EMPTY_TEXT),
        sa.Column("linked_idea_ids", JSONB(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("linked_card_ids", JSONB(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
    )
    op.create_table(
        "activity_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("last_used_at", TIMESTAMP),
        sa.Column("revoked_at", TIMESTAMP),
    )
    op.create_table(
        "api_token_projects",
        sa.Column(
            "token_id",
            sa.Text(),
            sa.ForeignKey("api_tokens.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            sa.Text(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "task_dependencies",
        sa.Column(
            "idea_id",
            sa.Text(),
            sa.ForeignKey("ideas.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "depends_on_idea_id",
            sa.Text(),
            sa.ForeignKey("ideas.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.CheckConstraint("idea_id <> depends_on_idea_id", name="task_dependencies_check"),
    )
    op.create_table(
        "idea_readiness_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "idea_id",
            sa.Text(),
            sa.ForeignKey("ideas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
    )

    op.create_index(
        "ideas_project_updated_idx", "ideas", ["project_id", sa.text("updated_at DESC")]
    )
    op.create_index(
        "board_cards_project_updated_idx",
        "board_cards",
        ["project_id", sa.text("updated_at DESC")],
    )
    op.create_index(
        "documents_project_updated_idx", "documents", ["project_id", sa.text("updated_at DESC")]
    )
    op.create_index("activity_events_created_idx", "activity_events", [sa.text("created_at DESC")])
    op.create_index("task_dependencies_depends_on_idx", "task_dependencies", ["depends_on_idea_id"])
    op.create_index(
        "idea_readiness_events_idea_idx",
        "idea_readiness_events",
        ["idea_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    for table in (
        "idea_readiness_events",
        "task_dependencies",
        "api_token_projects",
        "api_tokens",
        "activity_events",
        "documents",
        "board_cards",
        "ideas",
        "projects",
    ):
        op.drop_table(table)
