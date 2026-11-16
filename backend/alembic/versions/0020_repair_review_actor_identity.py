"""Add `task_reviews.actor_identity` where a renumbered migration left it out.

Revision 0014 adds this column. A build of an unmerged branch numbered its migrations
differently (0012 to 0015 there are review run reports, worker instances, suggested pull
requests and push requests), so a database that such a build migrated counts 0014 as
applied without ever having run it. Every later build then fails wherever it reads or
locks reviews, such as when a card is moved by hand.

The column is added only where it is missing, so databases that ran 0014 are untouched.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("alter table task_reviews add column if not exists actor_identity text")


def downgrade() -> None:
    """Nothing to undo: the column belongs to revision 0014, which removes it."""
