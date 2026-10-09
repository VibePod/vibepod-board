"""Push requests: the board asking a worker to push a card's branch to GitHub.

- `push_requests`: one row per request, with the branch and commit to push, the repository,
  why it was asked for (an approval or Open PR), its status and lease, and the outcome. At most
  one request per card is pending or claimed at a time.
- `board_cards.branch_pushed_sha` / `branch_pushed_at`: the commit a worker last reported
  pushed, and when.
- `projects.push_approved_branches`: whether the approval that moves a task to PR ready asks
  its reviewer to push the branch; on by default.

Existing cards have no pushed branch, and existing projects push approved branches.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("push_approved_branches", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("board_cards", sa.Column("branch_pushed_sha", sa.Text()))
    op.add_column("board_cards", sa.Column("branch_pushed_at", sa.DateTime(timezone=True)))
    op.create_table(
        "push_requests",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "card_id",
            sa.Text(),
            sa.ForeignKey("board_cards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Text(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("head_sha", sa.Text()),
        sa.Column("repository_local_path", sa.Text()),
        sa.Column("repository_remote_url", sa.Text()),
        sa.Column("repository", sa.Text()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("assignee", sa.Text()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("pushed_sha", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("open_pr", JSONB()),
        sa.Column("pull_request_url", sa.Text()),
        sa.Column("requested_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "push_requests_card_idx", "push_requests", ["card_id", sa.text("created_at DESC")]
    )
    op.create_index(
        "push_requests_active_card_key",
        "push_requests",
        ["card_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'claimed')"),
    )
    op.create_index(
        "push_requests_active_project_idx",
        "push_requests",
        ["project_id", "created_at"],
        postgresql_where=sa.text("status IN ('pending', 'claimed')"),
    )


def downgrade() -> None:
    op.drop_index("push_requests_active_project_idx", table_name="push_requests")
    op.drop_index("push_requests_active_card_key", table_name="push_requests")
    op.drop_index("push_requests_card_idx", table_name="push_requests")
    op.drop_table("push_requests")
    op.drop_column("board_cards", "branch_pushed_at")
    op.drop_column("board_cards", "branch_pushed_sha")
    op.drop_column("projects", "push_approved_branches")
