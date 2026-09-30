"""Review workers.

- `projects.required_approvals` / `max_review_rounds`: approvals a task needs for its head
  commit, and the rework verdicts in a row after which it is blocked.
- `board_cards.head_sha` / `handed_over_at` / `review_rounds`: the commit up for review, when
  it was handed over, and the rework verdicts in a row.
- `workers.mode`: implement or review.
- `task_reviews`: review claims and their verdicts.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("required_approvals", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "projects",
        sa.Column("max_review_rounds", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column("board_cards", sa.Column("head_sha", sa.Text()))
    op.add_column("board_cards", sa.Column("handed_over_at", sa.DateTime(timezone=True)))
    op.add_column(
        "board_cards",
        sa.Column("review_rounds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "workers",
        sa.Column("mode", sa.Text(), nullable=False, server_default=sa.text("'implement'")),
    )
    op.create_table(
        "task_reviews",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "idea_id", sa.Text(), sa.ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id",
            sa.Text(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("worker_id", sa.Text(), sa.ForeignKey("workers.id", ondelete="SET NULL")),
        sa.Column("head_sha", sa.Text()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verdict", sa.Text()),
        sa.Column("feedback", sa.Text()),
        sa.Column("ended_reason", sa.Text()),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "task_reviews_idea_idx", "task_reviews", ["idea_id", sa.text("created_at DESC")]
    )
    op.create_index(
        "task_reviews_open_idx",
        "task_reviews",
        ["project_id", "lease_expires_at"],
        postgresql_where=sa.text("ended_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("task_reviews_open_idx", table_name="task_reviews")
    op.drop_index("task_reviews_idea_idx", table_name="task_reviews")
    op.drop_table("task_reviews")
    op.drop_column("workers", "mode")
    op.drop_column("board_cards", "review_rounds")
    op.drop_column("board_cards", "handed_over_at")
    op.drop_column("board_cards", "head_sha")
    op.drop_column("projects", "max_review_rounds")
    op.drop_column("projects", "required_approvals")
