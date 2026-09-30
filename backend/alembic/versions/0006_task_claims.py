"""Claims, attempts and the blocked state on board cards, and the task history.

An automated runner claims a planned card: the claim is a lease (`claimed_at`,
`claim_expires_at`) held by the card's assignee. `attempts` counts failed automated runs,
and a blocked card (`blocked_at`, `blocked_reason`) stays in Planned but is skipped by
claims. `task_events` records what happened to a task under automation. Existing cards are
unclaimed, unblocked and have no attempts.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("board_cards", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.add_column("board_cards", sa.Column("claim_expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        "board_cards",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("board_cards", sa.Column("blocked_at", sa.DateTime(timezone=True)))
    op.add_column("board_cards", sa.Column("blocked_reason", sa.Text()))
    op.create_index(
        "board_cards_claim_expires_idx",
        "board_cards",
        ["claim_expires_at"],
        postgresql_where=sa.text("claim_expires_at IS NOT NULL"),
    )
    op.create_table(
        "task_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "idea_id", sa.Text(), sa.ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text()),
        sa.Column("message", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("task_events_idea_idx", "task_events", ["idea_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("task_events_idea_idx", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("board_cards_claim_expires_idx", table_name="board_cards")
    for column in ("blocked_reason", "blocked_at", "attempts", "claim_expires_at", "claimed_at"):
        op.drop_column("board_cards", column)
