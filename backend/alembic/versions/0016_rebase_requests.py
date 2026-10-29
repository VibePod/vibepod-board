"""Rebase requests.

- `board_cards.rebase_target` / `rebase_requested_by` / `rebase_requested_at`: a rebase of the
  card's branch asked for from the board, pending while the target is set.
- `board_cards.rebase_holder` / `rebase_claimed_at` / `rebase_expires_at`: the rebase worker
  holding the request and its lease.

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
    ("rebase_target", sa.Text()),
    ("rebase_requested_by", sa.Text()),
    ("rebase_requested_at", sa.DateTime(timezone=True)),
    ("rebase_holder", sa.Text()),
    ("rebase_claimed_at", sa.DateTime(timezone=True)),
    ("rebase_expires_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    for name, kind in COLUMNS:
        op.add_column("board_cards", sa.Column(name, kind))


def downgrade() -> None:
    for name, _kind in reversed(COLUMNS):
        op.drop_column("board_cards", name)
