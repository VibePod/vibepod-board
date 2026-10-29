"""What a sync pulls from a card's linked pull request.

The PR's title and description, its head branch and commit, when it was merged, when GitHub
last changed it, and the review decision (approved or changes_requested). Existing cards have
none until their next sync.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("github_pr_title", sa.Text),
    ("github_pr_body", sa.Text),
    ("github_pr_head", sa.Text),
    ("github_pr_head_sha", sa.Text),
    ("github_pr_merged_at", lambda: sa.DateTime(timezone=True)),
    ("github_pr_updated_at", lambda: sa.DateTime(timezone=True)),
    ("github_pr_review_decision", sa.Text),
)


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("board_cards", sa.Column(name, type_()))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("board_cards", name)
