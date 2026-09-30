from fastapi import APIRouter, status

from vibepod_board.api.deps import SettingsDep, worker_timing
from vibepod_board.api.models import HeartbeatRequest, WorkerRegistration
from vibepod_board.api.queries import ProjectFilter, project_filter
from vibepod_board.api.responses import Item
from vibepod_board.auth import AccessDep
from vibepod_board.db import SessionDep
from vibepod_board.schemas import Worker, WorkerList, WorkerSession
from vibepod_board.services import automation, workers

router = APIRouter(prefix="/api/workers", tags=["workers"])


@router.get("")
def list_workers(
    session: SessionDep,
    access: AccessDep,
    settings: SettingsDep,
    project_id: ProjectFilter = None,
) -> WorkerList:
    """Workers seen in the last day, connected ones first; `offline` once their heartbeats
    stopped or they signed off. For one project, also whether its automation is paused."""
    project = project_filter(project_id)
    items = workers.list_workers(session, access, project, worker_timing(settings))
    state = automation.get_automation(session, access, project) if project else None
    return WorkerList(items=items, automation=state)


@router.post("", status_code=status.HTTP_201_CREATED)
def register_worker(
    body: WorkerRegistration, session: SessionDep, access: AccessDep, settings: SettingsDep
) -> WorkerSession:
    return workers.register_worker(
        session,
        access,
        body.project_id,
        body.name,
        body.agent,
        body.machine,
        worker_timing(settings),
        body.mode,
    )


@router.get("/{worker_id}")
def get_worker(
    worker_id: str, session: SessionDep, access: AccessDep, settings: SettingsDep
) -> Item[Worker]:
    return Item(item=workers.get_worker(session, access, worker_id, worker_timing(settings)))


@router.post("/{worker_id}/heartbeat")
def heartbeat(
    worker_id: str,
    body: HeartbeatRequest,
    session: SessionDep,
    access: AccessDep,
    settings: SettingsDep,
) -> WorkerSession:
    """Keeps the worker online, records its status, task and step, renews the claims it
    holds, and answers with instructions for it."""
    return workers.heartbeat(
        session,
        access,
        worker_id,
        body.status,
        status_reason=body.status_reason,
        task=body.task,
        step=body.step,
        lease_seconds=body.lease_seconds,
        timing=worker_timing(settings),
    )


@router.post("/{worker_id}/sign-off")
def sign_off(
    worker_id: str, session: SessionDep, access: AccessDep, settings: SettingsDep
) -> Item[Worker]:
    return Item(
        item=workers.sign_off(
            session, access, worker_id, settings.claim_max_attempts, worker_timing(settings)
        )
    )


@router.post("/{worker_id}/stop")
def stop_worker(
    worker_id: str, session: SessionDep, access: AccessDep, settings: SettingsDep
) -> Item[Worker]:
    """Tells the worker, with its next heartbeat reply, to give its task back and sign off. A
    worker that is already offline is signed off at once."""
    return Item(
        item=workers.stop_worker(
            session, access, worker_id, settings.claim_max_attempts, worker_timing(settings)
        )
    )
