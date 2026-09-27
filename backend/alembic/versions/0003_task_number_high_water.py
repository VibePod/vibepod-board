"""Keep task numbers unique forever, and adopt GitHub links from the old sync endpoint.

- `projects.last_task_number` records the highest task number ever issued, so deleting
  the newest task does not hand its number to the next one.
- Tasks linked by the removed `/sync-github` endpoint have `github_issue_url` and
  `github_issue_number` but no `github_repository`; derive it from the URL. Repository names
  are stored lowercase, since GitHub treats them case-insensitively. A link that would
  duplicate another task's link in the same project is left without a repository.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ISSUE_URL = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(\d+)/?$", re.IGNORECASE
)


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("last_task_number", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        update projects set last_task_number = coalesce(
          (select max(task_number) from ideas where ideas.project_id = projects.id), 0)
        """
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            select id, project_id, github_issue_url, github_issue_number, github_repository
            from ideas
            where github_issue_number is not null
              and (github_repository is not null or github_issue_url is not null)
            order by created_at, id
            """
        )
    ).all()
    taken: set[tuple[str, str, int]] = set()
    for idea_id, project_id, url, number, repository in rows:
        if repository is None:
            match = ISSUE_URL.match((url or "").strip())
            if not match or int(match[2]) != number:
                continue
            repository = match[1]
        key = (project_id, repository.lower(), number)
        # Clear first, so the unique index never sees two rows with the same identity.
        connection.execute(
            sa.text("update ideas set github_repository = null where id = :id"), {"id": idea_id}
        )
        if key in taken:
            continue
        taken.add(key)
        connection.execute(
            sa.text("update ideas set github_repository = :repository where id = :id"),
            {"repository": key[1], "id": idea_id},
        )


def downgrade() -> None:
    op.drop_column("projects", "last_task_number")
