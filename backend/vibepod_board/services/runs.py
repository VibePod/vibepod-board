"""Run reports: every automated run adds one to its task, so the task keeps a history of its
attempts. Long text is cut down to fit: the verify output keeps its head and its tail, where
test runners print their summary."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, col, func, select

from vibepod_board.access import AccessContext, assert_can_access_project
from vibepod_board.enums import RunOutcome
from vibepod_board.errors import BadRequest
from vibepod_board.schemas import TaskRun, now
from vibepod_board.services.common import (
    add_activity,
    lock,
    new_id,
    normalize_optional_text,
    transactional,
)
from vibepod_board.services.references import require_idea_ref, task_keys
from vibepod_board.services.workers import require_worker
from vibepod_board.tables import IdeaRow, TaskRunRow

VERIFY_OUTPUT_HEAD = 2_000
VERIFY_OUTPUT_TAIL = 14_000
SUMMARY_LIMIT = 20_000
REASON_LIMIT = 4_000
COMMIT_LIMIT = 200


def truncate_middle(text: str, head: int, tail: int) -> tuple[str, bool]:
    """Keeps the first `head` and last `tail` characters of a long text."""
    if len(text) <= head + tail:
        return text, False
    omitted = len(text) - head - tail
    return f"{text[:head]}\n\n[… {omitted} characters omitted …]\n\n{text[-tail:]}", True


def truncate_end(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}\n\n[… truncated …]"


def task_run_from_row(row: TaskRunRow) -> TaskRun:
    return TaskRun.model_validate(row, from_attributes=True)


def _commits(commits: Sequence[Any]) -> list[dict[str, str]]:
    if len(commits) > COMMIT_LIMIT:
        raise BadRequest(f"A run report takes at most {COMMIT_LIMIT} commits")
    normalized = []
    for commit in commits:
        data = commit if isinstance(commit, dict) else commit.model_dump()
        sha = str(data.get("sha", "")).strip()
        if not sha:
            raise BadRequest("Every commit needs a sha")
        normalized.append({"sha": sha, "subject": str(data.get("subject") or "").strip()})
    return normalized


@transactional
def add_run_report(
    session: Session,
    access: AccessContext,
    reference: str,
    outcome: RunOutcome,
    summary: str | None = None,
    commits: Sequence[Any] = (),
    branch_name: str | None = None,
    verify_command: str | None = None,
    verify_exit_code: int | None = None,
    verify_output: str | None = None,
    duration_seconds: int | None = None,
    failure_reason: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    worker_id: str | None = None,
    worker_name: str | None = None,
    agent: str | None = None,
) -> TaskRun:
    idea = require_idea_ref(session, access, reference)
    assert_can_access_project(access, idea.project_id)
    outcome = RunOutcome(outcome)
    if duration_seconds is not None and duration_seconds < 0:
        raise BadRequest("durationSeconds must not be negative")
    resolved_worker_id = None
    if worker_id:
        worker = require_worker(session, access, worker_id)
        resolved_worker_id = worker.id
        if worker.project_id != idea.project_id:
            raise BadRequest("The worker belongs to another project")
        # The registered worker names the report, whatever name the caller sent along.
        worker_name = worker.name
        agent = agent or worker.agent or None
    output, truncated = (
        truncate_middle(verify_output, VERIFY_OUTPUT_HEAD, VERIFY_OUTPUT_TAIL)
        if verify_output
        else (None, False)
    )
    # Reports of a task keep the order they came in: one sent within the same millisecond as
    # the task's latest report lands just after it (as in the task history). The task is
    # locked first, so two reports arriving together take turns picking their timestamp.
    lock(session, IdeaRow, idea.id)
    timestamp = now()
    latest = session.exec(
        select(func.max(TaskRunRow.created_at)).where(TaskRunRow.idea_id == idea.id)
    ).one()
    if latest is not None and timestamp <= latest:
        timestamp = latest + timedelta(milliseconds=1)
    row = TaskRunRow(
        id=new_id(),
        idea_id=idea.id,
        worker_id=resolved_worker_id,
        worker_name=normalize_optional_text(worker_name),
        agent=normalize_optional_text(agent),
        outcome=outcome,
        summary=truncate_end((summary or "").strip(), SUMMARY_LIMIT),
        commits=_commits(commits),
        branch_name=normalize_optional_text(branch_name),
        verify_command=normalize_optional_text(verify_command),
        verify_exit_code=verify_exit_code,
        verify_output=output,
        verify_output_truncated=truncated,
        duration_seconds=duration_seconds,
        failure_reason=(
            truncate_end(failure_reason.strip(), REASON_LIMIT)
            if failure_reason and failure_reason.strip()
            else None
        ),
        started_at=started_at,
        finished_at=finished_at,
        created_at=timestamp,
    )
    session.add(row)
    session.flush()
    key = task_keys(session, [idea.id]).get(idea.id, idea.id)
    who = row.worker_name or "A runner"
    add_activity(session, "task.run", f"{who} reported a {outcome.value} run of {key}", timestamp)
    return task_run_from_row(row)


def list_run_reports(session: Session, access: AccessContext, reference: str) -> list[TaskRun]:
    """Every run of the task, newest first."""
    idea = require_idea_ref(session, access, reference)
    assert_can_access_project(access, idea.project_id)
    rows = session.exec(
        select(TaskRunRow)
        .where(TaskRunRow.idea_id == idea.id)
        .order_by(col(TaskRunRow.created_at).desc(), col(TaskRunRow.id).desc())
    ).all()
    return [task_run_from_row(row) for row in rows]
