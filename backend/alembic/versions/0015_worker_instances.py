"""Tell worker registrations apart.

- `workers.instance`: a short id of the running worker process, such as its container id, so
  restarts under the same name can be told apart.
- `workers.run_id`: the id of the agent container or task running the worker's current task,
  as `vp task logs` takes it.

Existing workers have neither.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workers", sa.Column("instance", sa.Text()))
    op.add_column("workers", sa.Column("run_id", sa.Text()))


def downgrade() -> None:
    op.drop_column("workers", "run_id")
    op.drop_column("workers", "instance")
