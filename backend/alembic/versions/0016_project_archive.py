"""Archive projects.

- `projects.archived_at`: set while the project is archived. Archived projects leave the
  active views and refuse writes until they are restored.

Existing projects stay active.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("archived_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("projects", "archived_at")
