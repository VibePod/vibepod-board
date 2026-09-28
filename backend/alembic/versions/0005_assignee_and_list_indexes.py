"""Task and card assignees, and indexes for filtered and paged lists.

`assignee` is free text naming whoever holds the work; the task owns it and its card
mirrors it. The indexes back the list filters (status, column, assignee) and the keyset
paging over `(updated_at, id)`. Existing rows stay unassigned.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `if not exists` throughout: a database the TypeScript server created on the agent-API
# branch already has these columns and indexes, under the same names, and is stamped at the
# baseline when it is adopted.
UPGRADE_SQL = """
alter table ideas add column if not exists assignee text;
alter table board_cards add column if not exists assignee text;
create index if not exists board_cards_idea_idx on board_cards(idea_id);
create index if not exists ideas_project_status_updated_idx
  on ideas(project_id, status, updated_at desc);
create index if not exists board_cards_project_column_updated_idx
  on board_cards(project_id, column_name, updated_at desc);
create index if not exists ideas_project_updated_id_idx
  on ideas(project_id, updated_at desc, id desc);
create index if not exists board_cards_project_updated_id_idx
  on board_cards(project_id, updated_at desc, id desc);
create index if not exists ideas_project_assignee_idx on ideas(project_id, assignee);
create index if not exists board_cards_project_assignee_idx on board_cards(project_id, assignee);
"""

INDEXES = (
    ("board_cards_project_assignee_idx", "board_cards"),
    ("ideas_project_assignee_idx", "ideas"),
    ("board_cards_project_updated_id_idx", "board_cards"),
    ("ideas_project_updated_id_idx", "ideas"),
    ("board_cards_project_column_updated_idx", "board_cards"),
    ("ideas_project_status_updated_idx", "ideas"),
    ("board_cards_idea_idx", "board_cards"),
)


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    for name, table in INDEXES:
        op.drop_index(name, table_name=table)
    op.drop_column("board_cards", "assignee")
    op.drop_column("ideas", "assignee")
