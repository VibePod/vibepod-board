"""Workers: automated runners connected to a project.

A worker registers when it starts, reports its status, current task and step with every
heartbeat, and signs off when it stops. A worker that stops sending heartbeats is shown as
offline.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Text(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("machine", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'idle'")),
        sa.Column("status_reason", sa.Text()),
        sa.Column("idea_id", sa.Text(), sa.ForeignKey("ideas.id", ondelete="SET NULL")),
        sa.Column("step", sa.Text()),
        sa.Column("task_started_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "workers_project_seen_idx", "workers", ["project_id", sa.text("last_seen_at DESC")]
    )


def downgrade() -> None:
    op.drop_index("workers_project_seen_idx", table_name="workers")
    op.drop_table("workers")
