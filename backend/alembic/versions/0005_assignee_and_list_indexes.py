"""Task and card assignees, and indexes for filtered and paged lists.

`assignee` is free text naming whoever holds the work; the task owns it and its card
mirrors it. The indexes back the list filters (status, column, assignee) and the keyset
paging over `(updated_at, id)`. Existing rows stay unassigned.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES: tuple[tuple[str, str, list], ...] = (
    ("board_cards_idea_idx", "board_cards", ["idea_id"]),
    (
        "ideas_project_status_updated_idx",
        "ideas",
        ["project_id", "status", sa.text("updated_at DESC")],
    ),
    (
        "board_cards_project_column_updated_idx",
        "board_cards",
        ["project_id", "column_name", sa.text("updated_at DESC")],
    ),
    (
        "ideas_project_updated_id_idx",
        "ideas",
        ["project_id", sa.text("updated_at DESC"), sa.text("id DESC")],
    ),
    (
        "board_cards_project_updated_id_idx",
        "board_cards",
        ["project_id", sa.text("updated_at DESC"), sa.text("id DESC")],
    ),
    ("ideas_project_assignee_idx", "ideas", ["project_id", "assignee"]),
    ("board_cards_project_assignee_idx", "board_cards", ["project_id", "assignee"]),
)


def upgrade() -> None:
    op.add_column("ideas", sa.Column("assignee", sa.Text()))
    op.add_column("board_cards", sa.Column("assignee", sa.Text()))
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    op.drop_column("board_cards", "assignee")
    op.drop_column("ideas", "assignee")
