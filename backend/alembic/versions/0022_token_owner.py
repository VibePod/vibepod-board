"""Tokens and sign-ins that belong to a user.

- `api_tokens.owner_user_id`: the user who created the token, or approved the sign-in it
  came from. Empty for the admin's tokens, which is every token that existed before. A
  user's tokens go with the user.
- `oauth_requests.owner_user_id`: the user who approved a sign-in, kept until the client
  collects its token.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("api_tokens", "oauth_requests"):
        op.add_column(
            table,
            sa.Column(
                "owner_user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    """Tokens of users become the admin's: nothing else tells them apart afterwards."""
    for table in ("oauth_requests", "api_tokens"):
        op.drop_column(table, "owner_user_id")
