"""Archive state on board cards.

A card finished in the done column can be archived: it leaves the board but keeps its
task, dependencies, readiness history and GitHub link. Existing cards stay active.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("board_cards", sa.Column("archived_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("board_cards", "archived_at")
