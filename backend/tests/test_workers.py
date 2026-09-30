"""Workers: runners register with a project, report what they do in heartbeats that keep
their claims alive, and sign off; silent workers show as offline."""

import threading
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
from vibepod_board.schemas import now
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


def test_heartbeats_leave_a_lapsed_claim_over(session: Session, vp) -> None:
    task = planned(session, vp, "Lapsed")
    worker = register(session).item
    claimed = claims.claim_task(session, ADMIN, "VP", NAME, lease_seconds=60).item.card
    lapsed = now() - timedelta(seconds=1)
    session.execute(
        text("update board_cards set claim_expires_at = :at where id = :id"),
        {"at": lapsed, "id": claimed.id},
    )
    session.commit()

    workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)

    assert board.get_card(session, ADMIN, task.id).claim_expires_at == lapsed
    assert claims.expire_claims(session) == 1
    assert board.get_card(session, ADMIN, task.id).column == BoardColumn.PLANNED


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


def test_concurrent_registrations_of_one_name_leave_one_connected(
    db: Engine, session: Session, vp
) -> None:
    runners = 8
    barrier = threading.Barrier(runners)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            with Session(db, expire_on_commit=False) as own:
                barrier.wait(timeout=10)
                register(own)
        except BaseException as error:  # noqa: BLE001 - surfaced by the assert below
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(runners)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    live = session.execute(
        text("select count(*) from workers where name = :name and stopped_at is null"),
        {"name": NAME},
    ).scalar_one()
    assert live == 1
    assert [worker.status for worker in listed(session)].count(WorkerState.IDLE) == 1


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


# --- races and edges found in review -------------------------------------------------


def test_a_late_sign_off_of_a_replaced_registration_leaves_the_claims_alone(
    session: Session, vp
) -> None:
    planned(session, vp, "Held across a restart")
    old = register(session).item
    new = register(session).item
    claims.claim_task(session, ADMIN, "VP", NAME)

    # The old process's shutdown hook runs after the new one registered.
    workers.sign_off(session, ADMIN, old.id)

    card = board.get_card(session, ADMIN, "VP-1")
    assert (card.column, card.assignee) == (BoardColumn.IN_PROGRESS, NAME)
    assert card.claimed_at is not None
    assert workers.get_worker(session, ADMIN, new.id).status == WorkerState.IDLE


def test_signing_off_skips_a_claim_that_ended_meanwhile(monkeypatch, session: Session, vp) -> None:
    from vibepod_board.services import workers as workers_module
    from vibepod_board.tables import BoardCardRow

    first = planned(session, vp, "Cancelled meanwhile")
    second = planned(session, vp, "Still held")
    worker = register(session).item
    claims.claim_task(session, ADMIN, "VP", NAME, task=first.id)
    claims.claim_task(session, ADMIN, "VP", NAME, task=second.id)
    real_release = workers_module.apply_release
    first_card = board.get_card(session, ADMIN, first.id).id

    def release_after_a_cancel(session_, access, card_id, *args):
        if card_id == first_card:
            session_.get(BoardCardRow, card_id).claimed_at = None
            session_.flush()
        return real_release(session_, access, card_id, *args)

    monkeypatch.setattr(workers_module, "apply_release", release_after_a_cancel)

    signed_off = workers.sign_off(session, ADMIN, worker.id)

    assert signed_off.status == WorkerState.OFFLINE
    assert board.get_card(session, ADMIN, second.id).column == BoardColumn.PLANNED


def test_a_heartbeat_for_a_deleted_task_records_the_worker_idle(session: Session, vp) -> None:
    task = planned(session, vp, "Deleted while worked on")
    worker = register(session).item
    claims.claim_task(session, ADMIN, "VP", NAME)
    workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)
    ideas.delete_idea(session, ADMIN, task.id)

    reply = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)

    assert (reply.item.status, reply.item.task_id) == (WorkerState.IDLE, None)


def test_an_offline_timeout_shorter_than_two_heartbeats_fails_at_startup(monkeypatch) -> None:
    from vibepod_board.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgres://x@localhost/x")
    monkeypatch.setenv("WORKER_HEARTBEAT_SECONDS", "30")
    monkeypatch.setenv("WORKER_OFFLINE_SECONDS", "30")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="at least twice WORKER_HEARTBEAT_SECONDS"):
            get_settings()
        monkeypatch.setenv("WORKER_OFFLINE_SECONDS", "60")
        get_settings.cache_clear()
        settings = get_settings()
        assert (settings.worker_heartbeat_seconds, settings.worker_offline_seconds) == (30, 60)
    finally:
        get_settings.cache_clear()


def test_a_sign_off_waiting_behind_an_edit_stamps_its_releases_after_it(
    db: Engine, session: Session, vp
) -> None:
    task = planned(session, vp, "Contended")
    worker = register(session).item
    card_id = claims.claim_task(session, ADMIN, "VP", NAME, task=task.id).item.card.id
    done: list[Any] = []

    def sign_off() -> None:
        with Session(db, expire_on_commit=False) as own:
            done.append(workers.sign_off(own, ADMIN, worker.id))

    with Session(db) as editor:
        editor.execute(
            text("select id from board_cards where id = :id for update"), {"id": card_id}
        )
        signer = threading.Thread(target=sign_off)
        signer.start()
        time.sleep(0.5)
        edited_at = now()
        editor.execute(
            text("update board_cards set updated_at = :at where id = :id"),
            {"at": edited_at, "id": card_id},
        )
        editor.commit()
    signer.join(timeout=30)

    assert len(done) == 1
    released = board.get_card(session, ADMIN, task.id)
    assert released.claimed_at is None
    assert released.updated_at > edited_at
