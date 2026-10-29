"""Strip terminal escape codes from stored run reports.

Runners sent what their agent printed, colours and cursor controls included. New reports are
cleaned when they come in; this cleans the ones stored before: summary, feedback, failure
reason, verify output and commit subjects. Text that was cut down keeps its truncation.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-29
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from vibepod_board.terminal_text import strip_terminal_codes

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT_COLUMNS = ("summary", "feedback", "failure_reason", "verify_output")


def _plain(text: str | None) -> str:
    """Trimmed as on ingest once cleaned; text without codes is left as it is."""
    text = text or ""
    cleaned = strip_terminal_codes(text)
    return cleaned.strip() if cleaned != text else text


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(f"SELECT id, {', '.join(TEXT_COLUMNS)}, commits FROM task_runs")
    ).mappings()
    for row in rows.all():
        commits = row["commits"] or []
        cleaned = {
            "summary": _plain(row["summary"]),
            # As on ingest: empty feedback and reasons are none, verify output keeps its spacing.
            "feedback": _plain(row["feedback"]) or None,
            "failure_reason": _plain(row["failure_reason"]) or None,
            "verify_output": strip_terminal_codes(row["verify_output"] or "") or None,
        }
        cleaned_commits = [
            {**commit, "subject": _plain(commit.get("subject"))} for commit in commits
        ]
        changed = [column for column in TEXT_COLUMNS if cleaned[column] != row[column]]
        if not changed and cleaned_commits == commits:
            continue
        assignments = [f"{column} = :{column}" for column in changed]
        assignments.append("commits = CAST(:commits AS jsonb)")
        connection.execute(
            sa.text(f"UPDATE task_runs SET {', '.join(assignments)} WHERE id = :id"),
            {
                **{column: cleaned[column] for column in changed},
                "commits": json.dumps(cleaned_commits),
                "id": row["id"],
            },
        )


def downgrade() -> None:
    # The escape codes are gone for good, and plain text is valid at the earlier revision too.
    pass
