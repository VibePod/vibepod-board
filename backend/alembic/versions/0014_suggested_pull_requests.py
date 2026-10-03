"""The pull request title and description an agent suggests for a board card.

The agent that hands a task over writes them, a later hand-over replaces them, and anyone can
edit them; opening the PR starts from them. Existing cards have none.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("suggested_pr_title", sa.Text),
    ("suggested_pr_body", sa.Text),
    ("suggested_pr_by", sa.Text),
    ("suggested_pr_at", lambda: sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("board_cards", sa.Column(name, type_()))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("board_cards", name)
