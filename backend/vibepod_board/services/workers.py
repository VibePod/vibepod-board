"""Workers: automated runners connected to a project.

A worker registers when it starts and then sends a heartbeat every few seconds with its status
(idle, working or paused), the task it works on and the step it is in. The board shows it as
offline once the heartbeats stop, and right away when it signs off.

The worker's name is the holder of the claims it takes (see `claims.py`), so every heartbeat
also renews those claims: a running worker never loses its task to claim expiry. Registering a
name that is already connected replaces the earlier registration, so a restarted worker takes
its claims back up. The heartbeat reply carries instructions for the worker.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, update
from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, AdminAccess, assert_can_access_project
from vibepod_board.enums import ReleaseOutcome, WorkerState, WorkerStatus, WorkerStep
from vibepod_board.errors import BadRequest, Conflict, NotFound
from vibepod_board.schemas import Worker, WorkerInstruction, WorkerSession, now
from vibepod_board.services.claims import DEFAULT_LEASE_SECONDS, apply_release, lease_for
from vibepod_board.services.common import (
    add_activity,
    new_id,
    normalize_optional_text,
    transactional,
)
from vibepod_board.services.listing import list_scope, scoped
from vibepod_board.services.references import (
    require_idea_ref,
    resolve_project_id,
    task_keys,
)
from vibepod_board.tables import BoardCardRow, IdeaRow, ProjectRow, WorkerRow

# Workers not seen for this long are left out of the list, and pruned after the retention.
WORKER_LIST_WINDOW = timedelta(hours=24)
WORKER_RETENTION = timedelta(days=7)


@dataclass(frozen=True)
class WorkerTiming:
    heartbeat_seconds: int = 15
    offline_seconds: int = 60
    # The lease a heartbeat renews the worker's claims by, unless it names one.
    lease_seconds: int = DEFAULT_LEASE_SECONDS


def _state(row: WorkerRow, timestamp: datetime, timing: WorkerTiming) -> WorkerState:
    silent = row.last_seen_at < timestamp - timedelta(seconds=timing.offline_seconds)
    if row.stopped_at is not None or silent:
        return WorkerState.OFFLINE
    return WorkerState(row.status)


def workers_from_rows(
    session: Session, rows: list[WorkerRow], timing: WorkerTiming, timestamp: datetime | None = None
) -> list[Worker]:
    timestamp = timestamp or now()
    idea_ids = list({row.idea_id for row in rows if row.idea_id})
    keys = task_keys(session, idea_ids)
    titles: dict[str, str] = {}
    if idea_ids:
        query = select(IdeaRow.id, IdeaRow.title).where(col(IdeaRow.id).in_(idea_ids))
        titles = {idea_id: title for idea_id, title in session.exec(query).all()}
    return [
        Worker(
            id=row.id,
            project_id=row.project_id,
            name=row.name,
            agent=row.agent,
            machine=row.machine,
            status=_state(row, timestamp, timing),
            status_reason=row.status_reason,
            task_id=row.idea_id,
            task_key=keys.get(row.idea_id) if row.idea_id else None,
            task_title=titles.get(row.idea_id) if row.idea_id else None,
            step=WorkerStep(row.step) if row.step else None,
            task_started_at=row.task_started_at,
            started_at=row.started_at,
            last_seen_at=row.last_seen_at,
            stopped_at=row.stopped_at,
        )
        for row in rows
    ]


def _can_see(access: AccessContext, project_id: str) -> bool:
    return isinstance(access, AdminAccess) or project_id in access.project_ids


def require_worker(
    session: Session, access: AccessContext, worker_id: str, for_update: bool = False
) -> WorkerRow:
    """A worker of the caller's projects; a foreign one is reported as missing."""
    query = select(WorkerRow).where(WorkerRow.id == worker_id.strip())
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.exec(query).first()
    if row is None or not _can_see(access, row.project_id):
        raise NotFound(f"Worker not found: {worker_id}")
    return row


def list_workers(
    session: Session,
    access: AccessContext,
    project: str | None = None,
    timing: WorkerTiming | None = None,
) -> list[Worker]:
    """Workers seen in the last day, connected ones first."""
    timing = timing or WorkerTiming()
    scope = list_scope(session, access, project)
    if scope == []:
        return []
    timestamp = now()
    rows = list(
        session.exec(
            scoped(select(WorkerRow), WorkerRow.project_id, scope)
            .where(col(WorkerRow.last_seen_at) >= timestamp - WORKER_LIST_WINDOW)
            .order_by(col(WorkerRow.name), col(WorkerRow.started_at).desc())
        ).all()
    )
    workers = workers_from_rows(session, rows, timing, timestamp)
    return sorted(workers, key=lambda worker: worker.status == WorkerState.OFFLINE)


def get_worker(
    session: Session, access: AccessContext, worker_id: str, timing: WorkerTiming | None = None
) -> Worker:
    row = require_worker(session, access, worker_id)
    return workers_from_rows(session, [row], timing or WorkerTiming())[0]


def _session_reply(
    session: Session,
    row: WorkerRow,
    timing: WorkerTiming,
    instructions: list[WorkerInstruction] | None = None,
) -> WorkerSession:
    session.flush()
    return WorkerSession(
        item=workers_from_rows(session, [row], timing)[0],
        instructions=instructions or [],
        heartbeat_seconds=timing.heartbeat_seconds,
    )


@transactional
def register_worker(
    session: Session,
    access: AccessContext,
    project: str,
    name: str,
    agent: str | None = None,
    machine: str | None = None,
    timing: WorkerTiming | None = None,
) -> WorkerSession:
    """Connects a worker to a project. It shows up as idle right away."""
    timing = timing or WorkerTiming()
    name = (name or "").strip()
    if not name:
        raise BadRequest("Worker name is required")
    project_id = resolve_project_id(session, project)
    assert_can_access_project(access, project_id)
    # Registrations of a project take turns, so two starting under one name never both stay
    # connected: the second one sees and replaces the first.
    project_row = session.exec(
        select(ProjectRow).where(ProjectRow.id == project_id).with_for_update()
    ).one()
    timestamp = now()
    session.execute(
        delete(WorkerRow).where(
            col(WorkerRow.project_id) == project_id,
            col(WorkerRow.last_seen_at) < timestamp - WORKER_RETENTION,
        )
    )
    # A restarted worker replaces its earlier registration and keeps its claims.
    session.execute(
        update(WorkerRow)
        .where(
            col(WorkerRow.project_id) == project_id,
            col(WorkerRow.name) == name,
            col(WorkerRow.stopped_at).is_(None),
        )
        .values(
            stopped_at=timestamp,
            status=WorkerStatus.IDLE,
            status_reason="Replaced by a new registration",
            idea_id=None,
            step=None,
            task_started_at=None,
        )
    )
    row = WorkerRow(
        id=new_id(),
        project_id=project_id,
        name=name,
        agent=(agent or "").strip(),
        machine=(machine or "").strip(),
        status=WorkerStatus.IDLE,
        started_at=timestamp,
        last_seen_at=timestamp,
    )
    session.add(row)
    # A restarted worker takes its claims back up: renew them now rather than at its first
    # heartbeat, which may come after they would have lapsed.
    _renew_claims(session, row, timestamp + timedelta(seconds=timing.lease_seconds))
    add_activity(
        session, "worker.registered", f"Worker {name} connected to {project_row.key}", timestamp
    )
    return _session_reply(session, row, timing)


def _renew_claims(session: Session, row: WorkerRow, expires_at: datetime) -> None:
    """Keeps the claims the worker holds alive. `updated_at` is left alone, like any
    renewal. A claim whose lease already ran out stays over: the sweep ends it and counts the
    attempt, as it does for a report from its holder (see `claims._held`)."""
    session.execute(
        update(BoardCardRow)
        .where(
            col(BoardCardRow.project_id) == row.project_id,
            col(BoardCardRow.assignee) == row.name,
            col(BoardCardRow.claimed_at).is_not(None),
            col(BoardCardRow.claim_expires_at) > now(),
        )
        .values(claim_expires_at=expires_at)
    )


def instructions_for(session: Session, row: WorkerRow) -> list[WorkerInstruction]:
    """What the board asks of the worker. Nothing yet; the heartbeat reply carries them."""
    return []


@transactional
def heartbeat(
    session: Session,
    access: AccessContext,
    worker_id: str,
    status: WorkerStatus,
    status_reason: str | None = None,
    task: str | None = None,
    step: WorkerStep | None = None,
    lease_seconds: int | None = None,
    timing: WorkerTiming | None = None,
) -> WorkerSession:
    """Records that the worker is alive, what it is doing, and renews its claims."""
    timing = timing or WorkerTiming()
    status = WorkerStatus(status)
    lease = lease_for(lease_seconds, timing.lease_seconds)
    row = require_worker(session, access, worker_id, for_update=True)
    if row.stopped_at is not None:
        raise Conflict(f"Worker {row.name} is signed off; register again to reconnect")
    reason = normalize_optional_text(status_reason)
    if status == WorkerStatus.PAUSED and not reason:
        raise BadRequest("A paused worker must say why: statusReason is required")
    timestamp = now()
    idea = None
    if status == WorkerStatus.WORKING:
        if not task:
            raise BadRequest("A working worker must name its task")
        try:
            idea = require_idea_ref(session, access, task)
        except NotFound:
            # Deleted while the worker was on it: the worker is not working on anything.
            status = WorkerStatus.IDLE
    if idea is not None:
        if idea.project_id != row.project_id:
            raise BadRequest(f"Task {task} is not in the worker's project")
        if row.idea_id != idea.id or row.status != WorkerStatus.WORKING:
            row.task_started_at = timestamp
        row.idea_id = idea.id
        row.step = WorkerStep(step) if step else None
    else:
        row.idea_id = None
        row.step = None
        row.task_started_at = None
    row.status = status
    row.status_reason = reason
    row.last_seen_at = timestamp
    _renew_claims(session, row, timestamp + lease)
    return _session_reply(session, row, timing, instructions_for(session, row))


@transactional
def sign_off(
    session: Session,
    access: AccessContext,
    worker_id: str,
    max_attempts: int = 3,
    timing: WorkerTiming | None = None,
) -> Worker:
    """Marks the worker offline right away. Claims it still holds go back to Planned without
    counting an attempt. A registration that was already replaced or signed off releases
    nothing: the claims under its name belong to whoever registered it since."""
    timing = timing or WorkerTiming()
    row = require_worker(session, access, worker_id, for_update=True)
    timestamp = now()
    live = row.stopped_at is None
    if live:
        row.stopped_at = timestamp
        row.last_seen_at = timestamp
    row.status = WorkerStatus.IDLE
    row.idea_id = None
    row.step = None
    row.task_started_at = None
    if not live:
        session.flush()
        return workers_from_rows(session, [row], timing, timestamp)[0]
    held = session.exec(
        select(BoardCardRow.id).where(
            BoardCardRow.project_id == row.project_id,
            BoardCardRow.assignee == row.name,
            col(BoardCardRow.claimed_at).is_not(None),
        )
    ).all()
    for card_id in held:
        try:
            apply_release(
                session,
                access,
                card_id,
                row.name,
                ReleaseOutcome.RELEASED,
                f"Worker {row.name} signed off",
                max_attempts,
            )
        except Conflict:
            # The claim ended since it was listed, such as by a cancel from the board.
            continue
    session.flush()
    return workers_from_rows(session, [row], timing, timestamp)[0]
