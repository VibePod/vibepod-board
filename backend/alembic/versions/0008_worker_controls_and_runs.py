"""Worker controls and run reports.

- `projects.automation_paused_at` / `automation_paused_reason`: automation paused from the
  board; claims are refused and workers are told to pause.
- `workers.stop_requested_at`: a worker asked from the board to stop.
- `task_runs`: one report per automated run of a task.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("automation_paused_at", sa.DateTime(timezone=True)))
    op.add_column("projects", sa.Column("automation_paused_reason", sa.Text()))
    op.add_column("workers", sa.Column("stop_requested_at", sa.DateTime(timezone=True)))
    op.create_table(
        "task_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "idea_id", sa.Text(), sa.ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("worker_id", sa.Text(), sa.ForeignKey("workers.id", ondelete="SET NULL")),
        sa.Column("worker_name", sa.Text()),
        sa.Column("agent", sa.Text()),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("commits", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("branch_name", sa.Text()),
        sa.Column("verify_command", sa.Text()),
        sa.Column("verify_exit_code", sa.Integer()),
        sa.Column("verify_output", sa.Text()),
        sa.Column(
            "verify_output_truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("task_runs_idea_idx", "task_runs", ["idea_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("task_runs_idea_idx", table_name="task_runs")
    op.drop_table("task_runs")
    op.drop_column("workers", "stop_requested_at")
    op.drop_column("projects", "automation_paused_reason")
    op.drop_column("projects", "automation_paused_at")
