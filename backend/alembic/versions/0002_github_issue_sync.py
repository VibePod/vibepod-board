"""GitHub issue sync state on ideas.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ideas", sa.Column("github_repository", sa.Text()))
    op.add_column("ideas", sa.Column("github_issue_state", sa.Text()))
    op.add_column("ideas", sa.Column("github_issue_updated_at", sa.DateTime(timezone=True)))
    op.add_column("ideas", sa.Column("github_synced_at", sa.DateTime(timezone=True)))
    # One issue maps to at most one task per project.
    op.create_index(
        "ideas_github_issue_key",
        "ideas",
        ["project_id", "github_repository", "github_issue_number"],
        unique=True,
        postgresql_where=sa.text(
            "github_repository IS NOT NULL AND github_issue_number IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ideas_github_issue_key", table_name="ideas")
    for column in (
        "github_synced_at",
        "github_issue_updated_at",
        "github_issue_state",
        "github_repository",
    ):
        op.drop_column("ideas", column)
