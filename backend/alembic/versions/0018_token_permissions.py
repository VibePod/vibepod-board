"""Per-area permissions for API tokens.

- `api_tokens.permissions`: the level a token has in each area, such as
  `{"tasks": "read", "board": "none", ...}`.
- `oauth_requests.permissions`: the permissions the admin chose when approving a sign-in,
  kept until the code is exchanged for the token.

Tokens and sign-ins that exist already get write access in every area, which is what every
token could do before.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FULL_ACCESS = sa.text(
    """'{"tasks": "write", "board": "write", "documents": "write", """
    """"github": "write", "automation": "write", "project": "write"}'::jsonb"""
)


def upgrade() -> None:
    for table in ("api_tokens", "oauth_requests"):
        op.add_column(
            table,
            sa.Column("permissions", JSONB(), nullable=False, server_default=FULL_ACCESS),
        )


def downgrade() -> None:
    for table in ("oauth_requests", "api_tokens"):
        op.drop_column(table, "permissions")
