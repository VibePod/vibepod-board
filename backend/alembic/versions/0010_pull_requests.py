"""The pull request linked to a board card.

A card in PR ready can open its pull request on GitHub, or link one opened by hand. The card
keeps the PR's identity and the state it had when it was opened or linked. Existing cards
have none.

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

COLUMNS = (
    ("github_pr_url", sa.Text),
    ("github_pr_number", sa.Integer),
    ("github_pr_repository", sa.Text),
    ("github_pr_state", sa.Text),
    ("github_pr_draft", sa.Boolean),
    ("github_pr_base", sa.Text),
    ("github_pr_synced_at", lambda: sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("board_cards", sa.Column(name, type_()))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("board_cards", name)
