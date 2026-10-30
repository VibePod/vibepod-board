"""Retiring projects: archive, restore and permanent deletion.

An archived project leaves the active views (the project list, pickers, unscoped lists) and
refuses writes, while reads still work. Archiving pauses its automation, so no worker can
claim work in it, and is refused while workers of the project are connected or its tasks are
claimed or under review. Restoring brings the project back with automation still paused.
Only an archived project can be deleted; deletion removes it and everything in it.
"""

from datetime import timedelta

from sqlalchemy import delete
from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, assert_can_access_project
from vibepod_board.errors import BadRequest, Conflict
from vibepod_board.schemas import DeletedProject, Project, now
from vibepod_board.services.common import (
    add_activity,
    project_from_row,
    transactional,
)
from vibepod_board.services.history import actor_for
from vibepod_board.services.references import resolve_project_id, task_keys
from vibepod_board.services.workers import WorkerTiming
from vibepod_board.tables import BoardCardRow, ProjectRow, TaskReviewRow, WorkerRow

ARCHIVED_REASON = "Project archived"


def _locked(session: Session, access: AccessContext, project: str) -> ProjectRow:
    project_id = resolve_project_id(session, project)
    assert_can_access_project(access, project_id)
    return session.exec(
        select(ProjectRow)
        .where(ProjectRow.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _busy(session: Session, row: ProjectRow, timing: WorkerTiming) -> str | None:
    """What keeps the project from being archived: connected workers and tasks that are
    claimed or under review, by name."""
    timestamp = now()
    workers = session.exec(
        select(WorkerRow.name)
        .where(
            WorkerRow.project_id == row.id,
            col(WorkerRow.stopped_at).is_(None),
            col(WorkerRow.last_seen_at) >= timestamp - timedelta(seconds=timing.offline_seconds),
        )
        .order_by(col(WorkerRow.name))
    ).all()
    claimed = session.exec(
        select(BoardCardRow.idea_id).where(
            BoardCardRow.project_id == row.id,
            col(BoardCardRow.claimed_at).is_not(None),
            col(BoardCardRow.claim_expires_at) > timestamp,
            col(BoardCardRow.idea_id).is_not(None),
        )
    ).all()
    reviewed = session.exec(
        select(TaskReviewRow.idea_id).where(
            TaskReviewRow.project_id == row.id,
            col(TaskReviewRow.ended_at).is_(None),
            col(TaskReviewRow.lease_expires_at) > timestamp,
        )
    ).all()
    reasons = []
    if workers:
        reasons.append(f"workers are connected ({', '.join(dict.fromkeys(workers))})")
    keys = task_keys(session, [idea_id for idea_id in claimed if idea_id])
    if keys:
        reasons.append(f"tasks are claimed ({', '.join(sorted(keys.values()))})")
    keys = task_keys(session, list(reviewed))
    if keys:
        reasons.append(f"tasks are under review ({', '.join(sorted(keys.values()))})")
    return " and ".join(reasons) or None


@transactional
def archive_project(
    session: Session, access: AccessContext, project: str, timing: WorkerTiming | None = None
) -> Project:
    """Archives the project and pauses its automation."""
    row = _locked(session, access, project)
    if row.archived_at is not None:
        raise Conflict(f"Project {row.key} is already archived")
    busy = _busy(session, row, timing or WorkerTiming())
    if busy:
        raise Conflict(f"Project {row.key} cannot be archived while {busy}; stop them first")
    timestamp = now()
    row.archived_at = timestamp
    if row.automation_paused_at is None:
        row.automation_paused_at = timestamp
        row.automation_paused_reason = ARCHIVED_REASON
    row.updated_at = timestamp
    session.flush()
    add_activity(
        session,
        "project.archived",
        f"{actor_for(session, access)} archived project {row.key}: {row.title}",
        timestamp,
    )
    return project_from_row(row)


@transactional
def restore_project(session: Session, access: AccessContext, project: str) -> Project:
    """Brings an archived project back. Its automation stays paused until resumed."""
    row = _locked(session, access, project)
    if row.archived_at is None:
        raise Conflict(f"Project {row.key} is not archived")
    timestamp = now()
    row.archived_at = None
    row.updated_at = timestamp
    session.flush()
    add_activity(
        session,
        "project.restored",
        f"{actor_for(session, access)} restored project {row.key}: {row.title}",
        timestamp,
    )
    return project_from_row(row)


@transactional
def delete_project(
    session: Session, access: AccessContext, project: str, confirm_key: str | None = None
) -> DeletedProject:
    """Deletes an archived project for good, with its tasks, cards, notes, workers and token
    links. `confirm_key`, when given, must be the project key."""
    row = _locked(session, access, project)
    if confirm_key is not None and confirm_key.strip().upper() != row.key:
        raise BadRequest(f"Type the project key {row.key} to confirm the deletion")
    if row.archived_at is None:
        raise Conflict(f"Project {row.key} is active; archive it before deleting it")
    deleted = DeletedProject(id=row.id, key=row.key, title=row.title)
    # The foreign keys cascade to everything in the project.
    session.execute(delete(ProjectRow).where(col(ProjectRow.id) == row.id))
    add_activity(
        session,
        "project.deleted",
        f"{actor_for(session, access)} deleted project {row.key}: {row.title}",
    )
    return deleted
