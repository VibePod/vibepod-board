"""Keep review run kind, verdict and feedback in reports.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_runs", sa.Column("kind", sa.Text(), nullable=False, server_default="implement")
    )
    op.add_column("task_runs", sa.Column("verdict", sa.Text(), nullable=True))
    op.add_column("task_runs", sa.Column("feedback", sa.Text(), nullable=True))
    op.execute(
        "UPDATE task_runs SET kind = workers.mode FROM workers "
        "WHERE task_runs.worker_id = workers.id"
    )

    # Review workers already reported runs before these fields existed. Bind each old
    # report to the latest review finished during that attempt, avoiding earlier rounds.
    op.execute("""
        UPDATE task_runs AS report
        SET verdict = matched.verdict, feedback = matched.feedback
        FROM (
            SELECT DISTINCT ON (run.id) run.id, review.verdict, review.feedback
            FROM task_runs AS run
            JOIN task_reviews AS review
              ON review.idea_id = run.idea_id AND review.worker_id = run.worker_id
             AND review.ended_at <= run.created_at
             AND review.updated_at >= COALESCE(
                 run.started_at,
                 (SELECT MAX(previous.created_at) FROM task_runs AS previous
                  WHERE previous.idea_id = run.idea_id AND previous.worker_id = run.worker_id
                    AND previous.created_at < run.created_at),
                 '-infinity'::timestamptz
             )
            WHERE run.kind = 'review'
            ORDER BY run.id, review.updated_at DESC, review.id DESC
        ) AS matched
        WHERE report.id = matched.id
    """)


def downgrade() -> None:
    op.drop_column("task_runs", "feedback")
    op.drop_column("task_runs", "verdict")
    op.drop_column("task_runs", "kind")
