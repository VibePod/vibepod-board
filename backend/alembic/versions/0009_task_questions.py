"""The question a blocked card waits on.

An automated run that cannot go on without an answer blocks its task with a question; the
card carries it until someone answers. Existing cards have none.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("board_cards", sa.Column("question", sa.Text()))


def downgrade() -> None:
    op.drop_column("board_cards", "question")
