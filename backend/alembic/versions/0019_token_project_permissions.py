"""Token permissions per project.

A token's permissions move from the token onto each project it is mapped to, so a token
that covers several projects can have different permissions in each:

- `api_token_projects.permissions`: the level the token has in each area of that project.
  Every mapping starts with the set its token had.
- `api_tokens.permissions` is dropped.
- `oauth_requests.permissions` now holds one set per approved project, keyed by project id.
  A sign-in that waits for its code keeps the set it was approved with, under each of its
  projects.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FULL_ACCESS = sa.text(
    """'{"tasks": "write", "board": "write", "documents": "write", """
    """"github": "write", "automation": "write", "project": "write"}'::jsonb"""
)
EMPTY = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.add_column(
        "api_token_projects",
        sa.Column("permissions", JSONB(), nullable=False, server_default=FULL_ACCESS),
    )
    op.execute(
        "update api_token_projects as mapped set permissions = token.permissions "
        "from api_tokens as token where token.id = mapped.token_id"
    )
    op.drop_column("api_tokens", "permissions")

    op.alter_column("oauth_requests", "permissions", server_default=EMPTY)
    op.execute(
        "update oauth_requests as request set permissions = coalesce("
        "(select jsonb_object_agg(project_id, request.permissions) "
        "from jsonb_array_elements_text(request.project_ids) as chosen(project_id)), "
        "'{}'::jsonb)"
    )


def downgrade() -> None:
    """A token gets one set again: the set of one of its projects, the first by project id.
    A token without projects, and every waiting sign-in, falls back to full access."""
    op.add_column(
        "api_tokens",
        sa.Column("permissions", JSONB(), nullable=False, server_default=FULL_ACCESS),
    )
    op.execute(
        "update api_tokens as token set permissions = mapped.permissions "
        "from (select distinct on (token_id) token_id, permissions from api_token_projects "
        "order by token_id, project_id) as mapped where mapped.token_id = token.id"
    )
    op.drop_column("api_token_projects", "permissions")
    op.alter_column("oauth_requests", "permissions", server_default=FULL_ACCESS)
    op.execute(sa.text("update oauth_requests set permissions = ").text + FULL_ACCESS.text)
