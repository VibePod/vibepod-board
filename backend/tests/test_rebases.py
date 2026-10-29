"""Rebase requests: asked for from the board on a card with a branch, claimed by one rebase
worker at a time, and reported as rebased, needing input or failed."""

import threading
from datetime import timedelta

import pytest
from conftest import call, login
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine, text
from sqlmodel import Session, select

from vibepod_board.access import admin_access
from vibepod_board.enums import (
    BoardColumn,
    ClaimMode,
    InstructionType,
    RebaseOutcome,
    RebaseTarget,
    ReleaseOutcome,
    ReviewVerdict,
    RunOutcome,
    TaskEventKind,
    WorkerStatus,
)
from vibepod_board.errors import BadRequest, Conflict
from vibepod_board.schemas import now
from vibepod_board.services import (
    automation,
    board,
    claims,
    conversation,
    history,
    ideas,
    projects,
    rebases,
    reviews,
    runs,
    tokens,
    transfer,
    workers,
)
from vibepod_board.tables import BoardCardRow

ADMIN = admin_access("admin")
RUNNER = "Claude::Runner"
REBASER = "Claude::Rebaser"
OTHER = "Codex::Rebaser"
REVIEWER = "Claude::Reviewer"
SHA = "a" * 40
REBASED_SHA = "c" * 40
DIVERGED = (
    "The branch vp-1 on origin (aaaa) and in /work (bbbb) have diverged; not picking one. "
    "Bring them together, then plan the task again"
)


@pytest.fixture
def vp(session: Session):
    return projects.create_project(session, "VP", "VibePod")


def planned(session: Session, project, title: str, branch: str | None = "vp-1"):
    task = ideas.create_idea(session, ADMIN, title=title, project_id=project.id)
    ideas.mark_ready(session, ADMIN, task.id)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED, branch_name=branch)
    return task


def diverged(session: Session, project, title: str = "Diverged"):
    """A planned task an implementation run blocked because its branch diverged."""
    task = planned(session, project, title)
    claims.claim_task(session, ADMIN, project.key, RUNNER, task=task.id)
    claims.release_task(session, ADMIN, task.id, RUNNER, ReleaseOutcome.BLOCKED, DIVERGED)
    return task


def in_review(session: Session, project, title: str):
    task = planned(session, project, title)
    claims.claim_task(session, ADMIN, project.key, RUNNER, task=task.id)
    claims.hand_over_task(session, ADMIN, task.id, RUNNER, branch_name="vp-1", head_sha=SHA)
    return task


def claim(session: Session, holder: str = REBASER, **options):
    return claims.claim_task(session, ADMIN, "VP", holder, mode=ClaimMode.REBASE, **options)


def card(session: Session, task):
    return board.get_card(session, ADMIN, task.id)


def kinds(session: Session, task) -> list[str]:
    return [event.kind for event in history.list_task_history(session, ADMIN, task.id)]


# --- requests ------------------------------------------------------------------------


def test_a_request_clears_the_diverged_block_and_is_shown_on_the_card(session: Session, vp) -> None:
    task = diverged(session, vp)
    assert card(session, task).blocked_reason == DIVERGED

    requested = rebases.request_rebase(session, ADMIN, "VP-1")

    assert requested.column == BoardColumn.PLANNED
    assert (requested.blocked_at, requested.blocked_reason) == (None, None)
    assert requested.rebase_target == RebaseTarget.BOTH
    assert requested.rebase_requested_by == "admin"
    assert requested.rebase_requested_at is not None
    assert requested.rebase_holder is None
    event = history.list_task_history(session, ADMIN, task.id)[0]
    assert event.kind == TaskEventKind.REBASE_REQUESTED
    assert "vp-1" in event.message and DIVERGED in event.message


def test_a_pending_rebase_keeps_implementation_claims_off_the_card(session: Session, vp) -> None:
    diverged(session, vp)
    rebases.request_rebase(session, ADMIN, "VP-1", RebaseTarget.ORIGIN)

    assert not claims.claim_task(session, ADMIN, "VP", RUNNER).claimed
    with pytest.raises(Conflict, match="a rebase of its branch is pending"):
        claims.claim_task(session, ADMIN, "VP", RUNNER, task="VP-1")


def test_requests_need_a_branch_in_planned_review_or_pr_ready(session: Session, vp) -> None:
    planned(session, vp, "No branch", branch=None)
    ready = ideas.create_idea(session, ADMIN, title="Ready", project_id=vp.id)
    ideas.mark_ready(session, ADMIN, ready.id)
    board.update_card(session, ADMIN, ready.id, branch_name="vp-2")

    with pytest.raises(Conflict, match="names no branch"):
        rebases.request_rebase(session, ADMIN, "VP-1")
    with pytest.raises(Conflict, match="only tasks in planned, review or PR ready"):
        rebases.request_rebase(session, ADMIN, "VP-2")


def test_asking_again_changes_the_target_until_a_worker_holds_it(session: Session, vp) -> None:
    diverged(session, vp)
    rebases.request_rebase(session, ADMIN, "VP-1", RebaseTarget.ORIGIN)

    changed = rebases.request_rebase(session, ADMIN, "VP-1", RebaseTarget.DEFAULT_BRANCH)
    assert changed.rebase_target == RebaseTarget.DEFAULT_BRANCH
    claim(session)

    with pytest.raises(Conflict, match=f"is being rebased by {REBASER}"):
        rebases.request_rebase(session, ADMIN, "VP-1")


def test_a_stale_request_is_refused(session: Session, vp) -> None:
    task = diverged(session, vp)
    seen = card(session, task).updated_at
    board.update_card(session, ADMIN, task.id, details="Edited meanwhile")

    with pytest.raises(Conflict):
        rebases.request_rebase(session, ADMIN, "VP-1", expected_updated_at=seen)


def test_cancelling_ends_the_request_and_tells_the_worker(session: Session, vp) -> None:
    task = diverged(session, vp)
    rebases.request_rebase(session, ADMIN, "VP-1")
    worker = workers.register_worker(session, ADMIN, "VP", REBASER, mode=ClaimMode.REBASE).item
    claim(session)
    beat = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)
    assert beat.instructions == []

    cancelled = rebases.cancel_rebase(session, ADMIN, "VP-1", "Fixed by hand")

    assert cancelled.rebase_target is None and cancelled.rebase_holder is None
    assert cancelled.column == BoardColumn.PLANNED and cancelled.blocked_at is None
    beat = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)
    assert [i.type for i in beat.instructions] == [InstructionType.CANCEL]
    assert "Fixed by hand" in (beat.instructions[0].reason or "")
    with pytest.raises(Conflict, match="no rebase request to cancel"):
        rebases.cancel_rebase(session, ADMIN, "VP-1")


def test_moving_the_card_by_hand_ends_the_request(session: Session, vp) -> None:
    task = in_review(session, vp, "Moved")
    rebases.request_rebase(session, ADMIN, "VP-1")

    moved = board.update_card(session, ADMIN, task.id, column=BoardColumn.DONE)

    assert moved.rebase_target is None
    event = history.list_task_history(session, ADMIN, task.id)[0]
    assert (event.kind, event.message) == (
        TaskEventKind.REBASE_ENDED,
        "Rebase ended: moved to done",
    )


# --- claims --------------------------------------------------------------------------


def test_the_oldest_request_is_claimed_by_one_worker(session: Session, vp) -> None:
    diverged(session, vp, "First")
    diverged(session, vp, "Second")
    rebases.request_rebase(session, ADMIN, "VP-2", RebaseTarget.DEFAULT_BRANCH)
    rebases.request_rebase(session, ADMIN, "VP-1")

    first = claim(session)
    second = claim(session, OTHER)

    assert first.item.task.key == "VP-2"
    assert first.rebase.target == RebaseTarget.DEFAULT_BRANCH
    assert (first.rebase.branch_name, first.rebase.holder) == ("vp-1", REBASER)
    assert first.item.card.column == BoardColumn.PLANNED
    assert first.item.card.assignee is None
    assert second.item.task.key == "VP-1"
    third = claim(session, "Third")
    assert (third.claimed, third.reason) == (False, rebases.NOTHING_TO_REBASE)
    with pytest.raises(Conflict, match=f"{REBASER} is rebasing it"):
        claim(session, "Third", task="VP-2")
    # The holder asking again renews its hold.
    assert claim(session, task="VP-2").item.task.key == "VP-2"


def test_only_pending_requests_are_claimed(session: Session, vp) -> None:
    diverged(session, vp)

    assert not claim(session).claimed
    with pytest.raises(Conflict, match="no rebase was requested"):
        claim(session, task="VP-1")


def test_concurrent_workers_never_hold_one_request(db: Engine, session: Session, vp) -> None:
    diverged(session, vp)
    rebases.request_rebase(session, ADMIN, "VP-1")
    contenders = 6
    barrier = threading.Barrier(contenders)
    results: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run(index: int) -> None:
        try:
            with Session(db, expire_on_commit=False) as own:
                barrier.wait(timeout=10)
                result = claims.claim_task(
                    own, ADMIN, "VP", f"Rebaser {index}", mode=ClaimMode.REBASE
                )
            with lock:
                results.append(result.claimed)
        except BaseException as error:  # noqa: BLE001 - surfaced by the assert below
            errors.append(error)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(contenders)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert results.count(True) == 1


def test_a_paused_project_hands_out_no_rebase(session: Session, vp) -> None:
    diverged(session, vp)
    rebases.request_rebase(session, ADMIN, "VP-1")
    automation.pause_automation(session, ADMIN, "VP", "Maintenance")

    result = claim(session)
    assert (result.claimed, result.paused) == (False, True)


def test_renewing_releasing_and_expiring_leave_the_request_pending(
    db: Engine, session: Session, vp
) -> None:
    task = diverged(session, vp)
    rebases.request_rebase(session, ADMIN, "VP-1")
    claim(session)

    renewed = rebases.renew_rebase(session, ADMIN, "VP-1", REBASER, lease_seconds=3600)
    assert renewed.rebase_expires_at > now() + timedelta(minutes=50)
    with pytest.raises(Conflict, match=f"not being rebased by {OTHER}"):
        rebases.renew_rebase(session, ADMIN, "VP-1", OTHER)

    released = rebases.release_rebase(session, ADMIN, "VP-1", REBASER, "Shutting down")
    assert released.rebase_target == RebaseTarget.BOTH and released.rebase_holder is None
    assert claim(session, OTHER).claimed

    with db.begin() as connection:
        connection.execute(
            text("update board_cards set rebase_expires_at = now() - interval '1 minute'")
        )
    with pytest.raises(Conflict, match="the hold expired"):
        rebases.complete_rebase(session, ADMIN, "VP-1", OTHER, RebaseOutcome.REBASED, REBASED_SHA)
    taken = claim(session, REBASER)
    assert taken.claimed and taken.rebase.holder == REBASER
    messages = [
        event.message
        for event in history.list_task_history(session, ADMIN, task.id)
        if event.kind == TaskEventKind.REBASE_ENDED
    ]
    assert messages == [
        f"Rebase by {OTHER} expired without a report; the request is pending again",
        f"Rebase by {REBASER} released: Shutting down",
    ]


def test_heartbeats_renew_and_sign_off_releases_the_hold(session: Session, vp) -> None:
    task = diverged(session, vp)
    rebases.request_rebase(session, ADMIN, "VP-1")
    worker = workers.register_worker(session, ADMIN, "VP", REBASER, mode=ClaimMode.REBASE).item
    assert worker.mode == ClaimMode.REBASE
    held = claim(session, lease_seconds=60).rebase.lease_expires_at

    workers.heartbeat(
        session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id, lease_seconds=3600
    )
    assert card(session, task).rebase_expires_at > held

    workers.sign_off(session, ADMIN, worker.id)
    after = card(session, task)
    assert (after.rebase_target, after.rebase_holder) == (RebaseTarget.BOTH, None)


# --- outcomes ------------------------------------------------------------------------


def test_a_rebased_planned_task_can_be_claimed_again(session: Session, vp) -> None:
    task = diverged(session, vp)
    rebases.request_rebase(session, ADMIN, "VP-1")
    claim(session)

    with pytest.raises(BadRequest, match="headSha is required"):
        rebases.complete_rebase(session, ADMIN, "VP-1", REBASER, RebaseOutcome.REBASED)
    rebased = rebases.complete_rebase(
        session, ADMIN, "VP-1", REBASER, RebaseOutcome.REBASED, REBASED_SHA, "Clean"
    )

    assert rebased.column == BoardColumn.PLANNED
    assert (rebased.blocked_at, rebased.rebase_target, rebased.rebase_holder) == (None, None, None)
    assert rebased.head_sha == REBASED_SHA
    assert claims.claim_task(session, ADMIN, "VP", RUNNER).item.task.key == "VP-1"
    assert kinds(session, task)[:4] == [
        TaskEventKind.CLAIMED,
        TaskEventKind.REBASED,
        TaskEventKind.REBASE_STARTED,
        TaskEventKind.REBASE_REQUESTED,
    ]
    rebased_event = history.list_task_history(session, ADMIN, task.id)[1]
    assert rebased_event.message == (
        f"Rebased by {REBASER} onto origin's task branch and the default branch at "
        f"{REBASED_SHA[:12]}: Clean"
    )
    with pytest.raises(Conflict, match="no rebase is pending"):
        rebases.complete_rebase(session, ADMIN, "VP-1", REBASER, RebaseOutcome.REBASED, REBASED_SHA)


def test_a_rebased_review_is_reviewed_again_at_its_new_head(session: Session, vp) -> None:
    task = in_review(session, vp, "Behind main")
    claims.claim_task(session, ADMIN, "VP", REVIEWER, mode=ClaimMode.REVIEW)
    reviews.submit_review(session, ADMIN, task.id, REVIEWER, ReviewVerdict.FAILED, None, "Flaky")
    claims.claim_task(session, ADMIN, "VP", "Other::Reviewer", mode=ClaimMode.REVIEW)

    rebases.request_rebase(session, ADMIN, "VP-1", RebaseTarget.DEFAULT_BRANCH)

    state = reviews.review_state(session, ADMIN, task.id)
    assert state.open_reviews == 0
    assert not claims.claim_task(session, ADMIN, "VP", REVIEWER, mode=ClaimMode.REVIEW).claimed
    claim(session)
    rebased = rebases.complete_rebase(
        session, ADMIN, "VP-1", REBASER, RebaseOutcome.REBASED, REBASED_SHA
    )
    assert (rebased.column, rebased.head_sha) == (BoardColumn.REVIEW, REBASED_SHA)
    again = claims.claim_task(session, ADMIN, "VP", REVIEWER, mode=ClaimMode.REVIEW)
    assert again.review.head_sha == REBASED_SHA


def test_a_rebased_pr_ready_card_stays_in_pr_ready(session: Session, vp) -> None:
    task = in_review(session, vp, "Waiting for its PR")
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PR_READY)
    rebases.request_rebase(session, ADMIN, "VP-1")
    claim(session)

    rebased = rebases.complete_rebase(
        session, ADMIN, "VP-1", REBASER, RebaseOutcome.REBASED, REBASED_SHA
    )

    assert (rebased.column, rebased.head_sha) == (BoardColumn.PR_READY, REBASED_SHA)


def test_needs_input_shows_the_question_and_the_answer_resumes_the_rebase(
    session: Session, vp
) -> None:
    task = in_review(session, vp, "Conflicts")
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PR_READY)
    rebases.request_rebase(session, ADMIN, "VP-1")
    claim(session)
    question = "Commit abc touches the same lines as main's def; keep which side?"

    with pytest.raises(BadRequest, match="note is required"):
        rebases.complete_rebase(session, ADMIN, "VP-1", REBASER, RebaseOutcome.NEEDS_INPUT)
    asked = rebases.complete_rebase(
        session, ADMIN, "VP-1", REBASER, RebaseOutcome.NEEDS_INPUT, note=question
    )

    assert asked.column == BoardColumn.PR_READY
    assert asked.question == question and asked.blocked_reason == f"Needs input: {question}"
    assert asked.rebase_target == RebaseTarget.BOTH and asked.rebase_holder is None
    assert not claim(session).claimed
    with pytest.raises(Conflict, match="answer it first"):
        rebases.request_rebase(session, ADMIN, "VP-1")

    answered = conversation.answer_question(session, ADMIN, task.id, "Keep main's side")

    assert answered.column == BoardColumn.PR_READY
    assert (answered.blocked_at, answered.question) == (None, None)
    assert answered.rebase_target == RebaseTarget.BOTH
    assert claim(session).item.task.key == "VP-1"
    assert TaskEventKind.ANSWER in kinds(session, task)


def asked_in_pr_ready(session: Session, project, title: str):
    """A PR-ready task whose rebase worker asked a question."""
    task = in_review(session, project, title)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PR_READY)
    rebases.request_rebase(session, ADMIN, "VP-1")
    claim(session)
    rebases.complete_rebase(
        session, ADMIN, "VP-1", REBASER, RebaseOutcome.NEEDS_INPUT, note="Keep which side?"
    )
    return task


def test_cancelling_a_rebase_that_asked_drops_its_question(session: Session, vp) -> None:
    task = asked_in_pr_ready(session, vp, "Asked, then cancelled")

    cancelled = rebases.cancel_rebase(session, ADMIN, "VP-1")

    assert cancelled.column == BoardColumn.PR_READY
    assert (cancelled.question, cancelled.blocked_at, cancelled.blocked_reason) == (None,) * 3
    assert cancelled.rebase_target is None
    with pytest.raises(Conflict):
        conversation.answer_question(session, ADMIN, task.id, "Too late")
    assert card(session, task).column == BoardColumn.PR_READY


def test_moving_a_rebase_that_asked_by_hand_drops_its_question(session: Session, vp) -> None:
    task = asked_in_pr_ready(session, vp, "Asked, then moved")

    moved = board.update_card(session, ADMIN, task.id, column=BoardColumn.REVIEW)

    assert moved.column == BoardColumn.REVIEW
    assert (moved.question, moved.blocked_at, moved.blocked_reason) == (None,) * 3
    assert moved.rebase_target is None


def test_ending_a_rebase_leaves_other_blocks_alone(session: Session, vp) -> None:
    task = in_review(session, vp, "Blocked elsewhere")
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PR_READY)
    rebases.request_rebase(session, ADMIN, "VP-1")
    row = session.exec(select(BoardCardRow).where(BoardCardRow.idea_id == task.id)).one()
    row.question, row.blocked_at, row.blocked_reason = "Which infra?", now(), "Waiting on infra"

    cancelled = rebases.cancel_rebase(session, ADMIN, "VP-1")

    assert (cancelled.question, cancelled.blocked_reason) == ("Which infra?", "Waiting on infra")


def test_a_failed_rebase_blocks_the_card_and_ends_the_request(session: Session, vp) -> None:
    task = diverged(session, vp)
    rebases.request_rebase(session, ADMIN, "VP-1")
    claim(session)

    with pytest.raises(BadRequest, match="note is required"):
        rebases.complete_rebase(session, ADMIN, "VP-1", REBASER, RebaseOutcome.FAILED)
    failed = rebases.complete_rebase(
        session, ADMIN, "VP-1", REBASER, RebaseOutcome.FAILED, note="origin is gone"
    )

    assert failed.blocked_reason == "Rebase failed: origin is gone"
    assert failed.rebase_target is None
    assert kinds(session, task)[0] == TaskEventKind.BLOCKED
    # It can be asked for again.
    assert rebases.request_rebase(session, ADMIN, "VP-1").blocked_at is None


def test_rebase_runs_are_reported_as_their_own_kind(session: Session, vp) -> None:
    task = diverged(session, vp)
    rebases.request_rebase(session, ADMIN, "VP-1")
    worker = workers.register_worker(session, ADMIN, "VP", REBASER, mode=ClaimMode.REBASE).item
    claim(session)
    rebases.complete_rebase(session, ADMIN, "VP-1", REBASER, RebaseOutcome.REBASED, REBASED_SHA)

    report = runs.add_run_report(
        session, ADMIN, task.id, RunOutcome.DONE, summary="Rebased onto main", worker_id=worker.id
    )

    assert report.kind == ClaimMode.REBASE
    listed = runs.list_run_reports(session, ADMIN, task.id).items
    assert [run.kind for run in listed] == [ClaimMode.REBASE]


def test_rebase_requests_stay_out_of_the_export(session: Session, vp) -> None:
    diverged(session, vp)
    rebases.request_rebase(session, ADMIN, "VP-1")
    claim(session)

    bundle = transfer.export_project(session, vp.id).model_dump(mode="json", by_alias=True)

    assert not any(key.startswith("rebase") for key in bundle["boardCards"][0])


# --- REST and MCP --------------------------------------------------------------------


def test_rest_runs_the_rebase_lifecycle(client: TestClient, session: Session, vp) -> None:
    diverged(session, vp)
    token = tokens.create_token(session, "Rebaser", [vp.id]).token
    headers = {"Authorization": f"Bearer {token}"}
    login(client)

    requested = client.post("/api/board/VP-1/rebase", json={"target": "origin"})
    assert requested.status_code == 200, requested.text
    item = requested.json()["item"]
    assert (item["rebaseTarget"], item["rebaseRequestedBy"]) == ("origin", "admin")
    assert "blockedAt" not in item
    assert client.post("/api/board/VP-1/rebase", json={"target": "sideways"}).status_code == 400

    claimed = client.post(
        "/api/board/claim",
        json={"projectId": "VP", "assignee": REBASER, "mode": "rebase"},
        headers=headers,
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["rebase"]["target"] == "origin"
    assert claimed.json()["rebase"]["branchName"] == "vp-1"
    other = client.post(
        "/api/board/claim",
        json={"projectId": "VP", "assignee": OTHER, "mode": "rebase"},
        headers=headers,
    )
    assert other.json()["claimed"] is False

    renewed = client.post(
        "/api/board/VP-1/rebase/renew",
        json={"assignee": REBASER, "leaseSeconds": 600},
        headers=headers,
    )
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["item"]["rebaseHolder"] == REBASER
    refused = client.post(
        "/api/board/VP-1/rebase/result",
        json={"assignee": OTHER, "outcome": "rebased", "headSha": REBASED_SHA},
        headers=headers,
    )
    assert refused.status_code == 409
    released = client.post(
        "/api/board/VP-1/rebase/release", json={"assignee": REBASER}, headers=headers
    )
    assert released.status_code == 200 and "rebaseHolder" not in released.json()["item"]
    client.post(
        "/api/board/claim",
        json={"projectId": "VP", "assignee": OTHER, "mode": "rebase"},
        headers=headers,
    )

    done = client.post(
        "/api/board/VP-1/rebase/result",
        json={"assignee": OTHER, "outcome": "rebased", "headSha": REBASED_SHA.upper()},
        headers=headers,
    )
    assert done.status_code == 200, done.text
    item = done.json()["item"]
    assert (item["column"], item["headSha"]) == ("planned", REBASED_SHA)
    assert "rebaseTarget" not in item

    assert client.post("/api/board/VP-1/rebase/cancel").status_code == 409
    client.post("/api/board/VP-1/rebase")
    cancelled = client.post("/api/board/VP-1/rebase/cancel", json={"reason": "Not needed"})
    assert cancelled.status_code == 200 and "rebaseTarget" not in cancelled.json()["item"]
    events = client.get("/api/ideas/VP-1/history").json()["items"]
    assert events[0]["kind"] == "rebase_ended"


async def test_mcp_runs_the_rebase_lifecycle(server_url: str, session: Session, vp) -> None:
    diverged(session, vp)
    token = tokens.create_token(session, "Rebaser", [vp.id]).token

    requested = await call(
        server_url, token, "request_rebase", id="VP-1", target="default_branch", view="full"
    )
    assert requested["item"]["rebaseTarget"] == "default_branch"
    assert "blockedReason" not in requested["item"]
    registered = await call(
        server_url, token, "register_worker", projectId="VP", name=REBASER, mode="rebase"
    )
    assert registered["item"]["mode"] == "rebase"
    claimed = await call(
        server_url, token, "claim_next_task", projectId="VP", assignee=REBASER, mode="rebase"
    )
    assert claimed["rebase"]["target"] == "default_branch"
    await call(server_url, token, "renew_task_rebase", id="VP-1", assignee=REBASER)

    with pytest.raises(ToolError, match="headSha is required"):
        await call(
            server_url, token, "report_rebase", id="VP-1", assignee=REBASER, outcome="rebased"
        )
    asked = await call(
        server_url,
        token,
        "report_rebase",
        id="VP-1",
        assignee=REBASER,
        outcome="needs_input",
        note="Drop commit abc?",
    )
    assert asked["item"]["question"] == "Drop commit abc?"
    await call(server_url, token, "answer_task_question", id="VP-1", answer="Keep it")
    await call(
        server_url, token, "claim_next_task", projectId="VP", assignee=REBASER, mode="rebase"
    )
    await call(server_url, token, "release_task_rebase", id="VP-1", assignee=REBASER)
    await call(
        server_url, token, "claim_next_task", projectId="VP", assignee=REBASER, mode="rebase"
    )
    rebased = await call(
        server_url,
        token,
        "report_rebase",
        id="VP-1",
        assignee=REBASER,
        outcome="rebased",
        headSha=REBASED_SHA,
        view="full",
    )
    assert rebased["item"]["headSha"] == REBASED_SHA
    assert "rebaseTarget" not in rebased["item"]
    await call(server_url, token, "request_rebase", id="VP-1")
    cancelled = await call(server_url, token, "cancel_rebase", id="VP-1")
    assert "rebaseTarget" not in cancelled["item"]
