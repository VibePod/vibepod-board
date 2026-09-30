"""Controls from the board (pause, stop, cancel) reach workers in their heartbeat reply, and
every automated run leaves a report on its task."""

from datetime import timedelta
from typing import Any

import pytest
from conftest import call, login
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine, text
from sqlmodel import Session

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import BoardColumn, RunOutcome, WorkerState, WorkerStatus
from vibepod_board.errors import BadRequest, Conflict, Forbidden, NotFound
from vibepod_board.schemas import now
from vibepod_board.services import (
    automation,
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


def working(session: Session, project) -> tuple[Any, Any]:
    """A registered worker that claimed a task and reported working on it."""
    task = planned(session, project, "Automated")
    worker = workers.register_worker(session, ADMIN, "VP", NAME, "claude", "laptop").item
    claims.claim_task(session, ADMIN, "VP", NAME)
    workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)
    return worker, task


def instruction_types(reply) -> list[str]:
    return [instruction.type for instruction in reply.instructions]


# --- pause and resume ----------------------------------------------------------------


def test_pausing_refuses_claims_until_resumed(session: Session, vp) -> None:
    planned(session, vp, "Waiting")

    state = automation.pause_automation(session, ADMIN, "VP", "Release freeze")
    assert (state.paused, state.reason) == (True, "Release freeze")
    assert state.paused_at is not None

    refused = claims.claim_task(session, ADMIN, "VP", NAME)
    assert (refused.claimed, refused.paused) == (False, True)
    assert refused.reason == "Automation of VP is paused: Release freeze"
    assert claims.claim_task(session, ADMIN, "VP", NAME, task="VP-1").paused is True

    resumed = automation.resume_automation(session, ADMIN, "VP")
    assert (resumed.paused, resumed.paused_at, resumed.reason) == (False, None, None)
    assert claims.claim_task(session, ADMIN, "VP", NAME).claimed is True


def test_paused_projects_tell_every_worker_to_pause(session: Session, vp) -> None:
    other = projects.create_project(session, "OT", "Other")
    worker = workers.register_worker(session, ADMIN, "VP", NAME).item
    elsewhere = workers.register_worker(session, ADMIN, other.id, "codex@desktop").item
    automation.pause_automation(session, ADMIN, "VP")

    reply = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE)
    assert instruction_types(reply) == ["pause"]
    assert reply.instructions[0].reason == automation.PAUSED_FROM_THE_BOARD
    assert workers.heartbeat(session, ADMIN, elsewhere.id, WorkerStatus.IDLE).instructions == []

    automation.resume_automation(session, ADMIN, "VP")
    assert workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE).instructions == []


def test_a_held_task_is_renewed_while_paused(session: Session, vp) -> None:
    task = planned(session, vp, "Running")
    claims.claim_task(session, ADMIN, "VP", NAME)
    automation.pause_automation(session, ADMIN, "VP")

    again = claims.claim_task(session, ADMIN, "VP", NAME, task=task.id)

    assert again.claimed is True


def test_only_callers_with_access_to_the_project_steer_it(session: Session, vp) -> None:
    other = projects.create_project(session, "OT", "Other")
    token = token_access("token-1", [vp.id])

    assert automation.pause_automation(session, token, "VP").paused is True
    with pytest.raises(Forbidden):
        automation.pause_automation(session, token, other.id)
    with pytest.raises(Forbidden):
        automation.get_automation(session, token, other.id)

    theirs = workers.register_worker(session, ADMIN, other.id, "codex@desktop").item
    with pytest.raises(NotFound):
        workers.stop_worker(session, token, theirs.id)
    task = planned(session, other, "Foreign")
    claims.claim_task(session, ADMIN, "OT", "codex@desktop")
    with pytest.raises(NotFound):
        claims.cancel_run(session, token, task.id)


# --- stop ----------------------------------------------------------------------------


def test_stopping_an_online_worker_asks_it_to_stop(session: Session, vp) -> None:
    worker, task = working(session, vp)

    stopped = workers.stop_worker(session, ADMIN, worker.id)

    assert stopped.stop_requested_at is not None
    assert stopped.status == WorkerState.WORKING
    reply = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)
    assert instruction_types(reply) == ["stop"]

    # The worker gives its task back and signs off.
    claims.release_task(session, ADMIN, task.id, NAME, "released", "Stopped from the board")
    signed_off = workers.sign_off(session, ADMIN, worker.id)
    assert signed_off.status == WorkerState.OFFLINE
    assert signed_off.stop_requested_at is None


def test_stopping_an_offline_worker_signs_it_off_at_once(db: Engine, session: Session, vp) -> None:
    worker, task = working(session, vp)
    with db.begin() as connection:
        connection.execute(text("update workers set last_seen_at = now() - interval '5 minutes'"))

    stopped = workers.stop_worker(session, ADMIN, worker.id)

    assert stopped.status == WorkerState.OFFLINE
    assert stopped.stopped_at is not None
    card = board.get_card(session, ADMIN, task.id)
    assert (card.column, card.claimed_at, card.attempts) == (BoardColumn.PLANNED, None, 0)


# --- cancel --------------------------------------------------------------------------


def test_cancelling_a_run_returns_the_task_and_tells_the_worker(session: Session, vp) -> None:
    worker, task = working(session, vp)

    cancelled = claims.cancel_run(session, ADMIN, task.id, "Wrong approach")

    assert cancelled.column == BoardColumn.PLANNED
    assert (cancelled.claimed_at, cancelled.assignee, cancelled.attempts) == (None, None, 0)
    event = history.list_task_history(session, ADMIN, task.id)[0]
    assert (event.kind, event.actor) == ("cancelled", "admin")
    assert event.message == f"Run by {NAME} cancelled: Wrong approach"

    reply = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)
    [instruction] = reply.instructions
    assert (instruction.type, instruction.task_id, instruction.task_key) == (
        "cancel",
        task.id,
        "VP-1",
    )
    assert instruction.reason == f"Run by {NAME} cancelled: Wrong approach"
    # Once the worker reports idle, the instruction is gone.
    assert workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE).instructions == []


def test_a_worker_whose_card_was_moved_by_hand_is_told_to_cancel(session: Session, vp) -> None:
    worker, task = working(session, vp)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.REVIEW)

    reply = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)

    assert instruction_types(reply) == ["cancel"]
    assert reply.instructions[0].reason == f"Claim by {NAME} ended: moved to review"


def test_a_worker_on_its_own_claim_gets_no_cancel(session: Session, vp) -> None:
    worker, task = working(session, vp)
    reply = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)
    assert reply.instructions == []


def test_a_runner_can_pass_over_the_task_it_saw_cancelled(session: Session, vp) -> None:
    worker, task = working(session, vp)
    planned(session, vp, "Next in line")
    claims.cancel_run(session, ADMIN, task.id)

    # Without the exclusion the cancelled task would come straight back first.
    again = claims.claim_task(session, ADMIN, "VP", NAME, exclude=["VP-1", "gone"])

    assert again.item.task.key == "VP-2"
    assert claims.claim_task(session, ADMIN, "VP", "other", exclude=[]).item.task.key == "VP-1"


def test_cancelling_needs_a_running_task(session: Session, vp) -> None:
    task = planned(session, vp, "Idle")
    with pytest.raises(Conflict, match="VP-1 has no run to cancel: it is not claimed"):
        claims.cancel_run(session, ADMIN, task.id)


# --- run reports ---------------------------------------------------------------------


def test_run_reports_keep_the_history_of_attempts(session: Session, vp) -> None:
    worker, task = working(session, vp)
    started = now() - timedelta(minutes=12)

    first = runs.add_run_report(
        session,
        ADMIN,
        task.id,
        RunOutcome.FAILED,
        summary="Tried the naive fix",
        verify_command="pytest",
        verify_exit_code=1,
        verify_output="1 failed",
        duration_seconds=720,
        failure_reason="Verify command failed",
        started_at=started,
        finished_at=started + timedelta(minutes=12),
        worker_id=worker.id,
    )
    second = runs.add_run_report(
        session,
        ADMIN,
        "VP-1",
        RunOutcome.DONE,
        summary="Fixed the login flow",
        commits=[{"sha": "a1b2c3d4", "subject": "Fix login"}],
        branch_name="vp-1",
        verify_command="pytest",
        verify_exit_code=0,
        verify_output="12 passed",
        duration_seconds=300,
        worker_name="claude@laptop",
        agent="claude",
    )

    assert first.worker_name == NAME
    assert first.agent == "claude"
    assert first.failure_reason == "Verify command failed"
    assert [run.id for run in runs.list_run_reports(session, ADMIN, task.id)] == [
        second.id,
        first.id,
    ]
    assert second.commits[0].sha == "a1b2c3d4"
    assert second.verify_output_truncated is False


def test_a_report_from_a_registered_worker_carries_its_name(session: Session, vp) -> None:
    worker, task = working(session, vp)

    report = runs.add_run_report(
        session, ADMIN, task.id, RunOutcome.DONE, worker_id=worker.id, worker_name="impostor"
    )

    assert report.worker_name == NAME


def test_long_verify_output_keeps_its_head_and_tail(session: Session, vp) -> None:
    task = planned(session, vp, "Noisy tests")
    output = "HEAD" + "x" * 50_000 + "TAIL: 3 failed"

    report = runs.add_run_report(session, ADMIN, task.id, RunOutcome.FAILED, verify_output=output)

    assert report.verify_output_truncated is True
    assert report.verify_output.startswith("HEAD")
    assert report.verify_output.endswith("TAIL: 3 failed")
    assert "characters omitted" in report.verify_output
    assert len(report.verify_output) < runs.VERIFY_OUTPUT_HEAD + runs.VERIFY_OUTPUT_TAIL + 100


def test_run_reports_validate_their_input(session: Session, vp) -> None:
    other = projects.create_project(session, "OT", "Other")
    task = planned(session, vp, "Validated")
    foreign_worker = workers.register_worker(session, ADMIN, other.id, "codex@desktop").item

    with pytest.raises(BadRequest, match="another project"):
        runs.add_run_report(session, ADMIN, task.id, RunOutcome.DONE, worker_id=foreign_worker.id)
    with pytest.raises(BadRequest, match="must not be negative"):
        runs.add_run_report(session, ADMIN, task.id, RunOutcome.DONE, duration_seconds=-1)
    with pytest.raises(NotFound):
        runs.add_run_report(session, token_access("t", [other.id]), task.id, RunOutcome.DONE)


# --- REST ----------------------------------------------------------------------------


@pytest.fixture
def api(client: TestClient) -> TestClient:
    login(client)
    assert client.post("/api/projects", json={"key": "VP", "title": "VibePod"}).status_code == 201
    task = client.post("/api/ideas", json={"projectId": "VP", "title": "Over REST"}).json()
    client.post(f"/api/ideas/{task['item']['id']}/ready")
    client.patch(f"/api/board/{task['item']['id']}", json={"column": "planned"})
    return client


def test_rest_pauses_stops_and_cancels(api: TestClient) -> None:
    worker = api.post("/api/workers", json={"projectId": "VP", "name": NAME}).json()["item"]

    paused = api.post("/api/projects/VP/automation/pause", json={"reason": "Freeze"}).json()
    assert (paused["paused"], paused["reason"]) == (True, "Freeze")
    listed = api.get("/api/workers", params={"projectId": "VP"}).json()
    assert listed["automation"]["paused"] is True
    assert "automation" not in api.get("/api/workers").json()
    refused = api.post("/api/board/claim", json={"projectId": "VP", "assignee": NAME}).json()
    assert refused["paused"] is True
    beat = api.post(f"/api/workers/{worker['id']}/heartbeat", json={"status": "idle"}).json()
    assert beat["instructions"] == [{"type": "pause", "reason": "Freeze"}]

    resumed = api.post("/api/projects/VP/automation/resume").json()
    assert resumed == {"projectId": resumed["projectId"], "paused": False}
    assert api.get("/api/projects/VP/automation").json()["paused"] is False

    api.post("/api/board/claim", json={"projectId": "VP", "assignee": NAME})
    api.post(f"/api/workers/{worker['id']}/heartbeat", json={"status": "working", "task": "VP-1"})
    cancelled = api.post("/api/board/VP-1/cancel", json={"reason": "Not now"})
    assert cancelled.status_code == 200
    assert cancelled.json()["item"]["column"] == "planned"
    assert api.post("/api/board/VP-1/cancel").status_code == 409
    beat = api.post(
        f"/api/workers/{worker['id']}/heartbeat", json={"status": "working", "task": "VP-1"}
    ).json()
    assert [(item["type"], item["taskKey"]) for item in beat["instructions"]] == [
        ("cancel", "VP-1")
    ]

    stopped = api.post(f"/api/workers/{worker['id']}/stop").json()["item"]
    assert stopped["stopRequestedAt"]
    beat = api.post(f"/api/workers/{worker['id']}/heartbeat", json={"status": "idle"}).json()
    assert [item["type"] for item in beat["instructions"]] == ["stop"]


def test_rest_records_and_lists_run_reports(api: TestClient) -> None:
    created = api.post(
        "/api/ideas/VP-1/runs",
        json={
            "outcome": "timed_out",
            "summary": "Ran out of time",
            "commits": [{"sha": "abc1234", "subject": "WIP"}],
            "durationSeconds": 7200,
            "failureReason": "Timed out after 2h",
            "startedAt": "2026-09-29T08:00:00Z",
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()["item"]
    assert (item["outcome"], item["durationSeconds"], item["failureReason"]) == (
        "timed_out",
        7200,
        "Timed out after 2h",
    )
    assert item["startedAt"] == "2026-09-29T08:00:00.000Z"
    assert [run["id"] for run in api.get("/api/ideas/VP-1/runs").json()["items"]] == [item["id"]]

    assert (
        api.post(
            "/api/ideas/VP-1/runs", json={"outcome": "done", "commits": [{"sha": "nope!"}]}
        ).status_code
        == 400
    )
    assert api.post("/api/ideas/VP-1/runs", json={"outcome": "maybe"}).status_code == 400


# --- MCP -----------------------------------------------------------------------------


async def test_mcp_steers_workers_and_reports_runs(server_url: str, session: Session, vp) -> None:
    worker, task = working(session, vp)
    token = tokens.create_token(session, "Runner", [vp.id]).token

    paused = await call(server_url, token, "pause_automation", projectId="VP", reason="Freeze")
    assert paused["paused"] is True
    listed = await call(server_url, token, "list_workers", projectId="VP")
    assert listed["automation"]["reason"] == "Freeze"
    resumed = await call(server_url, token, "resume_automation", projectId="VP")
    assert resumed["paused"] is False

    cancelled = await call(server_url, token, "cancel_task_run", id="VP-1", reason="Stop")
    assert cancelled["item"]["column"] == "planned"
    passed_over = await call(
        server_url, token, "claim_next_task", projectId="VP", assignee=NAME, exclude=["VP-1"]
    )
    assert passed_over["claimed"] is False
    with pytest.raises(ToolError, match="has no run to cancel"):
        await call(server_url, token, "cancel_task_run", id="VP-1")

    stopped = await call(server_url, token, "stop_worker", id=worker.id)
    assert stopped["item"]["stopRequestedAt"]

    report = await call(
        server_url,
        token,
        "add_run_report",
        id="VP-1",
        outcome="cancelled",
        summary="Stopped early",
        commits=[{"sha": "abc1234", "subject": "Partial"}],
        workerId=worker.id,
    )
    assert report["item"]["workerName"] == NAME
    reports = await call(server_url, token, "list_run_reports", id=task.id)
    assert [item["outcome"] for item in reports["items"]] == ["cancelled"]


# --- races and edges found in review -------------------------------------------------


def test_a_worker_on_a_deleted_task_is_told_to_cancel(session: Session, vp) -> None:
    worker, task = working(session, vp)
    ideas.delete_idea(session, ADMIN, task.id)

    reply = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)

    assert reply.item.status == WorkerState.IDLE
    [instruction] = reply.instructions
    assert (instruction.type, instruction.task_id, instruction.reason) == (
        "cancel",
        task.id,
        "The task was deleted",
    )


def test_a_cancel_for_a_run_the_caller_did_not_see_is_refused(session: Session, vp) -> None:
    worker, task = working(session, vp)
    seen = board.get_card(session, ADMIN, task.id)
    # The run the caller saw ended and another one started since.
    claims.release_task(session, ADMIN, task.id, NAME, "released")
    import time

    time.sleep(0.002)
    claims.claim_task(session, ADMIN, "VP", "codex@desktop")

    with pytest.raises(Conflict, match="Board card changed since"):
        claims.cancel_run(session, ADMIN, task.id, expected_updated_at=seen.updated_at)
    current = board.get_card(session, ADMIN, task.id)
    cancelled = claims.cancel_run(session, ADMIN, task.id, expected_updated_at=current.updated_at)
    assert cancelled.column == BoardColumn.PLANNED
