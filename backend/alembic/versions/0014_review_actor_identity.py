"""Separate manual approval identities from worker display names.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_reviews", sa.Column("actor_identity", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_reviews", "actor_identity")
