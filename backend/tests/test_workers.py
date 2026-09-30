"""Workers: runners register with a project, report what they do in heartbeats that keep
their claims alive, and sign off; silent workers show as offline. Workers that sign off,
are replaced by a restart or are removed by hand leave the board."""

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
from vibepod_board.services import (
    activity,
    board,
    claims,
    history,
    ideas,
    projects,
    runs,
    tokens,
    workers,
)

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


def activity_messages(session: Session) -> list[str]:
    return [event.message for event in activity.list_activity(session, ADMIN)]


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


def test_signing_off_removes_the_worker_and_releases_held_claims(session: Session, vp) -> None:
    task = planned(session, vp, "Held at shutdown")
    worker = register(session).item
    claims.claim_task(session, ADMIN, "VP", NAME)

    signed_off = workers.sign_off(session, ADMIN, worker.id)

    assert signed_off.status == WorkerState.OFFLINE
    assert signed_off.stopped_at is not None
    assert listed(session) == []
    assert f"Worker {NAME} ({worker.id[:8]}) removed: signed off" in activity_messages(session)
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
    with pytest.raises(NotFound, match="Worker not found"):
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


def test_pausing_without_a_reason_is_refused(session: Session, vp) -> None:
    worker = register(session).item

    for reason in (None, "  "):
        with pytest.raises(BadRequest, match="statusReason is required"):
            workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.PAUSED, status_reason=reason)


def test_registering_again_renews_the_claims_under_the_name(session: Session, vp) -> None:
    task = planned(session, vp, "Picked back up")
    register(session)
    claimed = claims.claim_task(session, ADMIN, "VP", NAME, lease_seconds=60).item.card

    register(session)

    renewed = board.get_card(session, ADMIN, task.id)
    assert renewed.claim_expires_at >= claimed.claim_expires_at + timedelta(seconds=600)
    assert renewed.updated_at == claimed.updated_at


def test_signing_off_releases_a_lapsed_claim_without_an_attempt(session: Session, vp) -> None:
    task = planned(session, vp, "Lapsed before the sign-off")
    worker = register(session).item
    claimed = claims.claim_task(session, ADMIN, "VP", NAME, lease_seconds=60).item.card
    session.execute(
        text("update board_cards set claim_expires_at = :at where id = :id"),
        {"at": now() - timedelta(seconds=1), "id": claimed.id},
    )
    session.commit()

    workers.sign_off(session, ADMIN, worker.id)

    released = board.get_card(session, ADMIN, task.id)
    assert (released.column, released.claimed_at, released.attempts) == (
        BoardColumn.PLANNED,
        None,
        0,
    )
    assert claims.expire_claims(session) == 0


def test_heartbeats_leave_other_holders_claims_alone(session: Session, vp) -> None:
    planned(session, vp, "Someone else's")
    worker = register(session).item
    claimed = claims.claim_task(session, ADMIN, "VP", "codex@desktop", lease_seconds=60)

    workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE, lease_seconds=600)

    card = board.get_card(session, ADMIN, "VP-1")
    assert card.claim_expires_at == claimed.item.card.claim_expires_at


def test_registering_a_name_again_removes_the_old_registration(
    db: Engine, session: Session, vp
) -> None:
    old = workers.register_worker(session, ADMIN, "VP", NAME, instance="c0ffee").item
    silent = register(session, "codex@desktop").item
    with db.begin() as connection:
        connection.execute(
            text("update workers set last_seen_at = now() - interval '5 minutes' where id = :id"),
            {"id": silent.id},
        )
    new = register(session).item
    restarted = register(session, "codex@desktop").item

    assert {worker.id for worker in listed(session)} == {new.id, restarted.id}
    with pytest.raises(NotFound):
        workers.heartbeat(session, ADMIN, old.id, WorkerStatus.IDLE)
    messages = activity_messages(session)
    assert f"Worker {NAME} (c0ffee) removed: replaced by a restart" in messages
    assert f"Worker codex@desktop ({silent.id[:8]}) removed: replaced by a restart" in messages


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
    assert session.execute(text("select count(*) from workers")).scalar_one() == 1
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


# --- telling workers apart and removing them -----------------------------------------


def go_silent(db: Engine, worker_id: str) -> None:
    with db.begin() as connection:
        connection.execute(
            text("update workers set last_seen_at = now() - interval '5 minutes' where id = :id"),
            {"id": worker_id},
        )


def test_workers_carry_their_instance_and_start_time(session: Session, vp) -> None:
    first = workers.register_worker(session, ADMIN, "VP", NAME, instance="  3f2a9c1b  ").item
    other = workers.register_worker(session, ADMIN, "VP", "codex@desktop", instance=" ").item

    assert (first.instance, other.instance) == ("3f2a9c1b", None)
    listed_by_id = {worker.id: worker for worker in listed(session)}
    assert listed_by_id[first.id].instance == "3f2a9c1b"
    assert listed_by_id[first.id].started_at == first.started_at
    assert workers.get_worker(session, ADMIN, first.id).instance == "3f2a9c1b"


def test_heartbeats_name_the_run_of_the_current_task(session: Session, vp) -> None:
    first = planned(session, vp, "First")
    second = planned(session, vp, "Second")
    worker = register(session).item

    beat = workers.heartbeat(
        session, ADMIN, worker.id, WorkerStatus.WORKING, task=first.id, run_id="vp-task-1"
    ).item
    assert beat.run_id == "vp-task-1"
    # A heartbeat on the same task without it keeps it; another task starts without one.
    same = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=first.id)
    assert same.item.run_id == "vp-task-1"
    moved_on = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=second.id)
    assert moved_on.item.run_id is None
    workers.heartbeat(
        session, ADMIN, worker.id, WorkerStatus.WORKING, task=second.id, run_id="vp-task-2"
    )
    assert workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE).item.run_id is None


def test_removing_a_connected_worker_is_refused(session: Session, vp) -> None:
    worker = register(session).item

    with pytest.raises(Conflict, match="is still connected; stop it first"):
        workers.remove_worker(session, ADMIN, worker.id)

    assert [item.id for item in listed(session)] == [worker.id]


def test_removing_an_offline_worker_frees_its_claims_and_keeps_its_reports(
    db: Engine, session: Session, vp
) -> None:
    task = planned(session, vp, "Held by a crashed worker")
    worker = workers.register_worker(session, ADMIN, "VP", NAME, instance="c0ffee").item
    claims.claim_task(session, ADMIN, "VP", NAME)
    workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)
    runs.add_run_report(session, ADMIN, task.id, "failed", worker_id=worker.id)
    go_silent(db, worker.id)

    removed = workers.remove_worker(session, ADMIN, worker.id)

    assert (removed.id, removed.status, removed.task_id) == (worker.id, WorkerState.OFFLINE, None)
    assert listed(session) == []
    card = board.get_card(session, ADMIN, task.id)
    assert (card.column, card.claimed_at, card.assignee, card.attempts) == (
        BoardColumn.PLANNED,
        None,
        None,
        0,
    )
    [report] = runs.list_run_reports(session, ADMIN, task.id).items
    assert (report.worker_id, report.worker_name) == (None, NAME)
    events = [event.message for event in history.list_task_history(session, ADMIN, task.id)]
    assert f"Released: Worker {NAME} was removed" in events
    assert f"Worker {NAME} (c0ffee) removed by admin" in activity_messages(session)
    with pytest.raises(NotFound, match="Worker not found"):
        workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE)
    with pytest.raises(NotFound):
        workers.remove_worker(session, ADMIN, worker.id)


def test_removing_offline_workers_leaves_connected_ones(db: Engine, session: Session, vp) -> None:
    projects.create_project(session, "OT", "Other")
    live = register(session).item
    silent = register(session, "codex@desktop").item
    elsewhere = register(session, "gemini@server", "OT").item
    go_silent(db, silent.id)
    go_silent(db, elsewhere.id)
    token = token_access("token-1", [vp.id])

    removed = workers.remove_offline_workers(session, token)

    assert [worker.id for worker in removed] == [silent.id]
    assert {worker.id for worker in listed(session)} == {live.id, elsewhere.id}
    assert [worker.id for worker in workers.remove_offline_workers(session, ADMIN, "OT")] == [
        elsewhere.id
    ]
    assert workers.remove_offline_workers(session, ADMIN) == []
    with pytest.raises(NotFound):
        workers.remove_worker(session, token, elsewhere.id)


def test_a_heartbeat_waiting_behind_a_removal_finds_the_worker_gone(
    db: Engine, session: Session, vp
) -> None:
    task = planned(session, vp, "Contended")
    worker = register(session).item
    card_id = claims.claim_task(session, ADMIN, "VP", NAME, task=task.id).item.card.id
    go_silent(db, worker.id)
    outcomes: dict[str, Any] = {}

    def remove() -> None:
        with Session(db, expire_on_commit=False) as own:
            outcomes["removed"] = workers.remove_worker(own, ADMIN, worker.id)

    def beat() -> None:
        with Session(db, expire_on_commit=False) as own:
            try:
                workers.heartbeat(own, ADMIN, worker.id, WorkerStatus.IDLE)
            except NotFound as error:
                outcomes["heartbeat"] = error

    # The removal takes the worker's lock, then waits on the card an editor holds; the late
    # heartbeat queues behind it.
    with Session(db) as editor:
        editor.execute(
            text("select id from board_cards where id = :id for update"), {"id": card_id}
        )
        remover = threading.Thread(target=remove)
        remover.start()
        time.sleep(0.5)
        beater = threading.Thread(target=beat)
        beater.start()
        time.sleep(0.5)
        editor.commit()
    remover.join(timeout=30)
    beater.join(timeout=30)

    assert outcomes["removed"].id == worker.id
    assert isinstance(outcomes["heartbeat"], NotFound)
    assert board.get_card(session, ADMIN, task.id).column == BoardColumn.PLANNED


def test_workers_gone_past_the_retention_are_pruned(db: Engine, session: Session, vp) -> None:
    old = register(session, "codex@desktop").item
    with db.begin() as connection:
        connection.execute(text("update workers set last_seen_at = now() - interval '8 days'"))

    register(session)

    count = session.execute(
        text("select count(*) from workers where id = :id"), {"id": old.id}
    ).scalar_one()
    assert count == 0


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
    assert api.get("/api/workers").json()["items"] == []
    assert api.get(f"/api/workers/{worker_id}").status_code == 404


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


def test_rest_removes_offline_workers(api: TestClient, db: Engine) -> None:
    live = api.post(
        "/api/workers", json={"projectId": "VP", "name": NAME, "instance": "c0ffee"}
    ).json()["item"]
    assert live["instance"] == "c0ffee"
    silent = api.post("/api/workers", json={"projectId": "VP", "name": "codex@desktop"}).json()
    gone = api.post("/api/workers", json={"projectId": "VP", "name": "gemini@server"}).json()
    beat = api.post(
        f"/api/workers/{live['id']}/heartbeat",
        json={"status": "idle", "runId": "ignored-while-idle"},
    ).json()["item"]
    assert "runId" not in beat

    refused = api.delete(f"/api/workers/{live['id']}")
    assert refused.status_code == 409
    assert "stop it first" in refused.json()["error"]

    with db.begin() as connection:
        connection.execute(
            text(
                "update workers set last_seen_at = now() - interval '5 minutes' where name <> :name"
            ),
            {"name": NAME},
        )
    removed = api.delete(f"/api/workers/{silent['item']['id']}")
    assert removed.status_code == 200, removed.text
    assert removed.json()["item"]["status"] == "offline"
    assert api.delete(f"/api/workers/{silent['item']['id']}").status_code == 404

    bulk = api.post("/api/workers/remove-offline", params={"projectId": "VP"})
    assert bulk.status_code == 200, bulk.text
    assert [worker["id"] for worker in bulk.json()["items"]] == [gone["item"]["id"]]
    assert [worker["id"] for worker in api.get("/api/workers").json()["items"]] == [live["id"]]


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
    with pytest.raises(ToolError, match="Worker not found"):
        await call(server_url, token, "worker_heartbeat", id=worker_id, status="idle")


async def test_mcp_removes_offline_workers(
    server_url: str, db: Engine, session: Session, vp
) -> None:
    task = planned(session, vp, "Over MCP")
    token = tokens.create_token(session, "Runner", [vp.id]).token
    registered = await call(
        server_url, token, "register_worker", projectId="VP", name=NAME, instance="c0ffee"
    )
    worker_id = registered["item"]["id"]
    await call(server_url, token, "claim_next_task", projectId="VP", assignee=NAME)
    beat = await call(
        server_url,
        token,
        "worker_heartbeat",
        id=worker_id,
        status="working",
        task="VP-1",
        runId="vp-task-7",
    )
    assert (beat["item"]["instance"], beat["item"]["runId"]) == ("c0ffee", "vp-task-7")
    other = await call(server_url, token, "register_worker", projectId="VP", name="codex@desktop")

    with pytest.raises(ToolError, match="stop it first"):
        await call(server_url, token, "remove_worker", id=worker_id)

    go_silent(db, worker_id)
    removed = await call(server_url, token, "remove_worker", id=worker_id)
    assert removed["item"]["status"] == "offline"
    assert board.get_card(session, ADMIN, task.id).attempts == 0
    go_silent(db, other["item"]["id"])
    bulk = await call(server_url, token, "remove_offline_workers", projectId="VP")
    assert [worker["id"] for worker in bulk["items"]] == [other["item"]["id"]]
    assert (await call(server_url, token, "list_workers"))["items"] == []


# --- races and edges found in review -------------------------------------------------


def test_a_late_sign_off_of_a_replaced_registration_leaves_the_claims_alone(
    session: Session, vp
) -> None:
    planned(session, vp, "Held across a restart")
    old = register(session).item
    new = register(session).item
    claims.claim_task(session, ADMIN, "VP", NAME)

    # The old process's shutdown hook runs after the new one registered: it is gone.
    with pytest.raises(NotFound):
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

    def release_after_a_cancel(session_, access, card_id, *args, **kwargs):
        if card_id == first_card:
            session_.get(BoardCardRow, card_id).claimed_at = None
            session_.flush()
        return real_release(session_, access, card_id, *args, **kwargs)

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


def test_a_lease_shorter_than_two_heartbeats_fails_at_startup(monkeypatch) -> None:
    from vibepod_board.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgres://x@localhost/x")
    monkeypatch.setenv("WORKER_HEARTBEAT_SECONDS", "60")
    monkeypatch.setenv("WORKER_OFFLINE_SECONDS", "180")
    monkeypatch.setenv("CLAIM_LEASE_SECONDS", "90")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="CLAIM_LEASE_SECONDS must be at least twice"):
            get_settings()
        monkeypatch.setenv("CLAIM_LEASE_SECONDS", "120")
        get_settings.cache_clear()
        assert get_settings().claim_lease_seconds == 120
    finally:
        get_settings.cache_clear()
