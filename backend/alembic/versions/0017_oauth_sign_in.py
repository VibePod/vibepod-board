"""OAuth sign-in for MCP clients.

- `oauth_clients`: clients that registered themselves (dynamic client registration).
- `oauth_requests`: sign-ins in progress. A request gets a one-time code and the chosen
  projects when the admin approves it, and is deleted when the code is exchanged for a
  token, when it is denied, or after it expired.

The token a sign-in issues is an ordinary row in `api_tokens`.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY_LIST = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.Text(), primary_key=True),
        sa.Column("info", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "oauth_requests",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "client_id",
            sa.Text(),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("code_challenge", sa.Text(), nullable=False),
        sa.Column("state", sa.Text()),
        sa.Column("scopes", JSONB(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("resource", sa.Text()),
        sa.Column("code_hash", sa.Text(), unique=True),
        sa.Column("project_ids", JSONB(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("oauth_requests")
    op.drop_table("oauth_clients")
