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
    """A token gets one set again. It must not reach more than before in any project, so
    each area gets the lowest level the token had in any of its projects. Sign-ins that
    wait for a decision or for their code are dropped for the same reason: one set could
    not say what was approved per project, and the client simply signs in again. A token
    without projects reaches nothing and keeps the column default."""
    op.add_column(
        "api_tokens",
        sa.Column("permissions", JSONB(), nullable=False, server_default=FULL_ACCESS),
    )
    op.execute(
        "update api_tokens as token set permissions = lowest.permissions from ("
        "  select token_id, jsonb_object_agg(area, level) as permissions from ("
        "    select mapped.token_id, area, (array['none', 'read', 'write'])[min(coalesce("
        "      array_position(array['none', 'read', 'write'], mapped.permissions ->> area), 1"
        "    ))] as level"
        "    from api_token_projects as mapped"
        "    cross join unnest(array["
        "      'tasks', 'board', 'documents', 'github', 'automation', 'project'"
        "    ]) as area"
        "    group by mapped.token_id, area"
        "  ) as per_area group by token_id"
        ") as lowest where lowest.token_id = token.id"
    )
    op.drop_column("api_token_projects", "permissions")
    op.execute("delete from oauth_requests")
    op.alter_column("oauth_requests", "permissions", server_default=FULL_ACCESS)
