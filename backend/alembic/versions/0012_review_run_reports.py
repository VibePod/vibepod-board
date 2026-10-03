"""Keep review run kind, verdict and feedback in reports.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_runs", sa.Column("kind", sa.Text(), nullable=False, server_default="implement")
    )
    op.add_column("task_runs", sa.Column("verdict", sa.Text(), nullable=True))
    op.add_column("task_runs", sa.Column("feedback", sa.Text(), nullable=True))
    op.execute(
        "UPDATE task_runs SET kind = workers.mode FROM workers "
        "WHERE task_runs.worker_id = workers.id"
    )


def downgrade() -> None:
    op.drop_column("task_runs", "feedback")
    op.drop_column("task_runs", "verdict")
    op.drop_column("task_runs", "kind")
