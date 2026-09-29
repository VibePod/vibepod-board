"""Workers: runners register with a project, report what they do in heartbeats that keep
their claims alive, and sign off; silent workers show as offline."""

import time
from datetime import timedelta
from typing import Any

import pytest
from conftest import call, login
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine, text
from sqlmodel import Session

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import BoardColumn, WorkerState, WorkerStatus, WorkerStep
from vibepod_board.errors import BadRequest, Conflict, NotFound
from vibepod_board.services import board, claims, history, ideas, projects, tokens, workers

ADMIN = admin_access("admin")
NAME = "claude@laptop"


@pytest.fixture
def vp(session: Session):
    return projects.create_project(session, "VP", "VibePod")


def planned(session: Session, project, title: str, **fields: Any):
    task = ideas.create_idea(session, ADMIN, title=title, project_id=project.id, **fields)
    ideas.mark_ready(session, ADMIN, task.id)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED)
    return task


def register(session: Session, name: str = NAME, project: str = "VP"):
    return workers.register_worker(session, ADMIN, project, name, "claude", "laptop")


def listed(session: Session, access=ADMIN, **options: Any):
    return workers.list_workers(session, access, **options)


def test_a_registered_worker_shows_up_idle(session: Session, vp) -> None:
    reply = register(session)

    assert reply.heartbeat_seconds == 15
    assert reply.instructions == []
    [worker] = listed(session, project="VP")
    assert worker.id == reply.item.id
    assert (worker.name, worker.agent, worker.machine) == (NAME, "claude", "laptop")
    assert worker.status == WorkerState.IDLE
    assert worker.last_seen_at == worker.started_at


def test_heartbeats_report_the_task_the_step_and_since_when(session: Session, vp) -> None:
    first = planned(session, vp, "Implement login")
    second = planned(session, vp, "Implement logout")
    worker = register(session).item

    working = workers.heartbeat(
        session,
        ADMIN,
        worker.id,
        WorkerStatus.WORKING,
        task="VP-1",
        step=WorkerStep.PREPARING_WORKSPACE,
    ).item
    assert working.status == WorkerState.WORKING
    assert (working.task_id, working.task_key, working.task_title) == (
        first.id,
        "VP-1",
        "Implement login",
    )
    assert working.step == WorkerStep.PREPARING_WORKSPACE
    started = working.task_started_at
    assert started is not None

    time.sleep(0.002)
    running = workers.heartbeat(
        session,
        ADMIN,
        worker.id,
        WorkerStatus.WORKING,
        task=first.id,
        step=WorkerStep.AGENT_RUNNING,
    ).item
    assert running.step == WorkerStep.AGENT_RUNNING
    assert running.task_started_at == started
    assert running.last_seen_at > working.last_seen_at

    moved_on = workers.heartbeat(
        session, ADMIN, worker.id, WorkerStatus.WORKING, task=second.id
    ).item
    assert moved_on.task_key == "VP-2"
    assert moved_on.task_started_at > started

    idle = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE).item
    assert (idle.status, idle.task_id, idle.step, idle.task_started_at) == (
        WorkerState.IDLE,
        None,
        None,
        None,
    )


def test_a_paused_worker_says_why(session: Session, vp) -> None:
    worker = register(session).item
    paused = workers.heartbeat(
        session, ADMIN, worker.id, WorkerStatus.PAUSED, status_reason="Usage limit reached"
    ).item
    assert (paused.status, paused.status_reason) == (WorkerState.PAUSED, "Usage limit reached")


def test_validates_heartbeats(session: Session, vp) -> None:
    other = projects.create_project(session, "OT", "Other")
    foreign = ideas.create_idea(session, ADMIN, title="Elsewhere", project_id=other.id)
    worker = register(session).item

    with pytest.raises(BadRequest, match="must name its task"):
        workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING)
    with pytest.raises(BadRequest, match="is not in the worker's project"):
        workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=foreign.id)
    with pytest.raises(NotFound, match="Worker not found"):
        workers.heartbeat(session, ADMIN, "missing", WorkerStatus.IDLE)
    with pytest.raises(BadRequest, match="Worker name is required"):
        workers.register_worker(session, ADMIN, "VP", "  ")


def test_silent_workers_show_as_offline(db: Engine, session: Session, vp) -> None:
    worker = register(session).item
    timing = workers.WorkerTiming(offline_seconds=60)
    assert listed(session, timing=timing)[0].status == WorkerState.IDLE

    with db.begin() as connection:
        connection.execute(
            text("update workers set last_seen_at = now() - interval '61 seconds'"),
        )

    assert listed(session, timing=timing)[0].status == WorkerState.OFFLINE
    # A heartbeat brings it back.
    workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE)
    assert listed(session, timing=timing)[0].status == WorkerState.IDLE


def test_workers_gone_for_a_day_leave_the_list(db: Engine, session: Session, vp) -> None:
    register(session)
    with db.begin() as connection:
        connection.execute(text("update workers set last_seen_at = now() - interval '25 hours'"))
    assert listed(session) == []


def test_signing_off_shows_offline_at_once_and_releases_held_claims(session: Session, vp) -> None:
    task = planned(session, vp, "Held at shutdown")
    worker = register(session).item
    claims.claim_task(session, ADMIN, "VP", NAME)

    signed_off = workers.sign_off(session, ADMIN, worker.id)

    assert signed_off.status == WorkerState.OFFLINE
    assert signed_off.stopped_at is not None
    card = board.get_card(session, ADMIN, task.id)
    assert (card.column, card.claimed_at, card.assignee, card.attempts) == (
        BoardColumn.PLANNED,
        None,
        None,
        0,
    )
    assert history.list_task_history(session, ADMIN, task.id)[0].message == (
        f"Released: Worker {NAME} signed off"
    )
    with pytest.raises(Conflict, match="is signed off; register again"):
        workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE)


def test_heartbeats_keep_the_workers_claims_alive(session: Session, vp) -> None:
    task = planned(session, vp, "Long run")
    worker = register(session).item
    claimed = claims.claim_task(session, ADMIN, "VP", NAME, lease_seconds=60).item.card

    workers.heartbeat(
        session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id, lease_seconds=600
    )

    renewed = board.get_card(session, ADMIN, task.id)
    assert renewed.claim_expires_at >= claimed.claim_expires_at + timedelta(seconds=500)
    assert renewed.updated_at == claimed.updated_at
    assert claims.expire_claims(session, timestamp=claimed.claim_expires_at) == 0
    assert board.get_card(session, ADMIN, task.id).column == BoardColumn.IN_PROGRESS


def test_heartbeats_leave_other_holders_claims_alone(session: Session, vp) -> None:
    planned(session, vp, "Someone else's")
    worker = register(session).item
    claimed = claims.claim_task(session, ADMIN, "VP", "codex@desktop", lease_seconds=60)

    workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE, lease_seconds=600)

    card = board.get_card(session, ADMIN, "VP-1")
    assert card.claim_expires_at == claimed.item.card.claim_expires_at


def test_registering_a_connected_name_replaces_the_old_registration(session: Session, vp) -> None:
    old = register(session).item
    new = register(session).item

    names = [(worker.id, worker.status) for worker in listed(session)]
    assert (new.id, WorkerState.IDLE) in names
    assert (old.id, WorkerState.OFFLINE) in names
    with pytest.raises(Conflict):
        workers.heartbeat(session, ADMIN, old.id, WorkerStatus.IDLE)


def test_workers_are_visible_only_within_the_token_projects(session: Session, vp) -> None:
    other = projects.create_project(session, "OT", "Other")
    mine = register(session).item
    theirs = register(session, "codex@desktop", "OT").item
    token = token_access("token-1", [vp.id])

    assert [worker.id for worker in listed(session, token)] == [mine.id]
    assert {worker.id for worker in listed(session)} == {mine.id, theirs.id}
    with pytest.raises(NotFound):
        workers.get_worker(session, token, theirs.id)
    with pytest.raises(NotFound):
        workers.heartbeat(session, token, theirs.id, WorkerStatus.IDLE)
    with pytest.raises(NotFound):
        workers.sign_off(session, token, theirs.id)
    from vibepod_board.errors import Forbidden

    with pytest.raises(Forbidden):
        workers.register_worker(session, token, other.id, "sneaky")
    with pytest.raises(Forbidden):
        listed(session, token, project="OT")


# --- REST ----------------------------------------------------------------------------


@pytest.fixture
def api(client: TestClient) -> TestClient:
    login(client)
    assert client.post("/api/projects", json={"key": "VP", "title": "VibePod"}).status_code == 201
    task = client.post("/api/ideas", json={"projectId": "VP", "title": "Over REST"}).json()
    client.post(f"/api/ideas/{task['item']['id']}/ready")
    client.patch(f"/api/board/{task['item']['id']}", json={"column": "planned"})
    return client


def test_rest_runs_a_worker_session(api: TestClient) -> None:
    registered = api.post(
        "/api/workers",
        json={"projectId": "VP", "name": NAME, "agent": "claude", "machine": "laptop"},
    )
    assert registered.status_code == 201, registered.text
    body = registered.json()
    worker_id = body["item"]["id"]
    assert body["heartbeatSeconds"] == 15
    assert body["instructions"] == []
    assert body["item"]["status"] == "idle"

    api.post("/api/board/claim", json={"projectId": "VP", "assignee": NAME})
    beat = api.post(
        f"/api/workers/{worker_id}/heartbeat",
        json={"status": "working", "task": "VP-1", "step": "agent_running"},
    )
    assert beat.status_code == 200, beat.text
    item = beat.json()["item"]
    assert (item["status"], item["taskKey"], item["step"]) == ("working", "VP-1", "agent_running")
    assert item["taskStartedAt"]

    listed = api.get("/api/workers", params={"projectId": "VP"}).json()["items"]
    assert [worker["name"] for worker in listed] == [NAME]
    assert api.get(f"/api/workers/{worker_id}").json()["item"]["taskTitle"] == "Over REST"

    assert (
        api.post(f"/api/workers/{worker_id}/heartbeat", json={"status": "sleeping"}).status_code
        == 400
    )

    stopped = api.post(f"/api/workers/{worker_id}/sign-off")
    assert stopped.json()["item"]["status"] == "offline"
    assert api.get("/api/board/VP-1").json()["item"]["column"] == "planned"


def test_rest_scopes_workers_to_the_token_projects(api: TestClient) -> None:
    api.post("/api/projects", json={"key": "OT", "title": "Other"})
    projects_by_key = {p["key"]: p["id"] for p in api.get("/api/projects").json()["items"]}
    token = api.post(
        "/api/tokens", json={"name": "Runner", "projectIds": [projects_by_key["VP"]]}
    ).json()["token"]
    foreign = api.post("/api/workers", json={"projectId": "OT", "name": "admin-worker"}).json()
    api.post("/api/auth/logout")
    headers = {"Authorization": f"Bearer {token}"}

    assert api.get("/api/workers", headers=headers).json()["items"] == []
    assert api.get(f"/api/workers/{foreign['item']['id']}", headers=headers).status_code == 404
    assert (
        api.post("/api/workers", json={"projectId": "OT", "name": "x"}, headers=headers).status_code
        == 403
    )


# --- MCP -----------------------------------------------------------------------------


async def test_mcp_runs_a_worker_session(server_url: str, session: Session, vp) -> None:
    planned(session, vp, "Over MCP")
    token = tokens.create_token(session, "Runner", [vp.id]).token

    registered = await call(
        server_url, token, "register_worker", projectId="VP", name=NAME, agent="claude"
    )
    worker_id = registered["item"]["id"]
    assert registered["heartbeatSeconds"] == 15

    await call(server_url, token, "claim_next_task", projectId="VP", assignee=NAME)
    beat = await call(
        server_url,
        token,
        "worker_heartbeat",
        id=worker_id,
        status="working",
        task="VP-1",
        step="verifying",
    )
    assert (beat["item"]["taskKey"], beat["item"]["step"]) == ("VP-1", "verifying")
    assert beat["instructions"] == []

    listed = await call(server_url, token, "list_workers", projectId="VP")
    assert [worker["status"] for worker in listed["items"]] == ["working"]

    signed_off = await call(server_url, token, "sign_off_worker", id=worker_id)
    assert signed_off["item"]["status"] == "offline"
    with pytest.raises(ToolError, match="is signed off"):
        await call(server_url, token, "worker_heartbeat", id=worker_id, status="idle")
