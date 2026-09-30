"""Automated claims: runners take planned tasks in work order, hold them on a lease, and hand
them over to Review or give them back to Planned, blocked after too many failures."""

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
from vibepod_board.bundle import parse_project_bundle
from vibepod_board.enums import BoardColumn, ReleaseOutcome
from vibepod_board.errors import BadRequest, Conflict, Forbidden, NotFound
from vibepod_board.schemas import now
from vibepod_board.services import board, claims, history, ideas, projects, readiness, transfer

ADMIN = admin_access("admin")
RUNNER = "Claude::Runner::laptop"
OTHER = "Codex::Runner::desktop"


@pytest.fixture
def vp(session: Session):
    return projects.create_project(session, "VP", "VibePod")


def planned(session: Session, project, title: str, **fields: Any):
    """A task whose card sits in Planned, ready to be claimed."""
    task = ideas.create_idea(session, ADMIN, title=title, project_id=project.id, **fields)
    ideas.mark_ready(session, ADMIN, task.id)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED)
    return task


def claim(session: Session, holder: str = RUNNER, **options: Any):
    return claims.claim_task(session, ADMIN, "VP", holder, **options)


def claimed_key(result) -> str | None:
    return result.item.task.key if result.claimed else None


def card(session: Session, task):
    return board.get_card(session, ADMIN, task.id)


def kinds(session: Session, task) -> list[str]:
    return [event.kind for event in history.list_task_history(session, ADMIN, task.id)]


# --- claiming ------------------------------------------------------------------------


def test_claims_the_next_planned_task_in_work_order(session: Session, vp) -> None:
    first = planned(session, vp, "Schema")
    dependent = planned(session, vp, "API", depends_on=[first.id])
    planned(session, vp, "Docs")

    assert claimed_key(claim(session)) == "VP-1"
    # VP-2 waits for VP-1, so the next claim skips it.
    assert claimed_key(claim(session, OTHER)) == "VP-3"
    assert claim(session, "Third").claimed is False

    board.update_card(session, ADMIN, first.id, column=BoardColumn.DONE)
    assert claimed_key(claim(session, "Third")) == "VP-2"
    assert card(session, dependent).assignee == "Third"


def test_a_claim_moves_the_card_and_names_the_holder_and_since_when(session: Session, vp) -> None:
    task = planned(session, vp, "Claimable")
    before = now()

    result = claim(session, lease_seconds=600)

    assert result.claimed
    claimed_card = card(session, task)
    assert claimed_card.column == BoardColumn.IN_PROGRESS
    assert claimed_card.assignee == RUNNER
    assert ideas.get_idea(session, ADMIN, task.id).assignee == RUNNER
    assert claimed_card.claimed_at >= before
    assert claimed_card.claim_expires_at == claimed_card.claimed_at + timedelta(seconds=600)
    assert result.item.card.claimed_at == claimed_card.claimed_at
    assert result.item.task.title == "Claimable"
    assert kinds(session, task) == ["claimed"]


def test_skips_tasks_that_are_not_planned_held_denied_or_blocked(session: Session, vp) -> None:
    ideas.mark_ready(session, ADMIN, ideas.create_idea(session, ADMIN, "Ready", vp.id).id)
    held = planned(session, vp, "Held by a person")
    ideas.update_idea(session, ADMIN, held.id, assignee="Alice")
    denied = planned(session, vp, "Denied")
    ideas.update_idea(session, ADMIN, denied.id, status="denied")
    blocked = planned(session, vp, "Blocked")
    claim(session, task=blocked.id)
    claims.release_task(session, ADMIN, blocked.id, RUNNER, ReleaseOutcome.BLOCKED, "Unclear")

    result = claim(session)
    assert result.claimed is False
    assert result.reason == claims.NOTHING_TO_CLAIM


def test_never_claims_a_task_whose_dependencies_are_open(session: Session, vp) -> None:
    blocker = ideas.create_idea(session, ADMIN, title="Unfinished", project_id=vp.id)
    task = planned(session, vp, "Waiting", depends_on=[blocker.id])

    assert claim(session).claimed is False
    with pytest.raises(Conflict, match="VP-2 cannot be claimed: it waits for 1 unfinished dep"):
        claim(session, task=task.id)

    ideas.update_idea(session, ADMIN, blocker.id, status="denied")
    assert claimed_key(claim(session)) == "VP-2"


def test_claims_only_tasks_with_enough_readiness_when_asked(session: Session, vp) -> None:
    low = planned(session, vp, "Vague")
    high = planned(session, vp, "Precise")
    planned(session, vp, "Unscored")
    readiness.set_idea_readiness(session, ADMIN, low.id, 4, "Missing criteria")
    readiness.set_idea_readiness(session, ADMIN, high.id, 8, "Clear")

    assert claimed_key(claim(session, min_readiness=7)) == "VP-2"
    assert claim(session, OTHER, min_readiness=7).claimed is False
    with pytest.raises(Conflict, match=r"its readiness \(4\) is below 7"):
        claim(session, OTHER, task=low.id, min_readiness=7)
    # Without the filter the rest is fair game.
    assert claimed_key(claim(session, OTHER)) == "VP-1"


def test_claims_only_labelled_tasks_when_asked(session: Session, vp) -> None:
    planned(session, vp, "Unmarked")
    planned(session, vp, "Marked", labels=["Agent", "backend"])
    planned(session, vp, "Half marked", labels=["agent"])

    assert claimed_key(claim(session, labels=["agent", "BACKEND"])) == "VP-2"
    assert claimed_key(claim(session, OTHER, labels=["agent"])) == "VP-3"
    assert claim(session, "Third", labels=["agent"]).claimed is False


def test_claims_a_named_task_and_explains_a_refusal(session: Session, vp) -> None:
    planned(session, vp, "First")
    second = planned(session, vp, "Second")

    assert claimed_key(claim(session, task="VP-2")) == "VP-2"
    with pytest.raises(Conflict, match="VP-2 cannot be claimed: its card is in in progress"):
        claim(session, OTHER, task=second.id)


def test_asking_again_for_a_held_task_renews_the_claim(session: Session, vp) -> None:
    task = planned(session, vp, "Resumed")
    first = claim(session, task=task.id)
    time.sleep(0.002)

    again = claim(session, task=task.id)

    assert again.claimed
    assert again.item.card.claimed_at == first.item.card.claimed_at
    assert again.item.card.claim_expires_at > first.item.card.claim_expires_at
    assert kinds(session, task) == ["claimed"]


def test_validates_the_claim_request(session: Session, vp) -> None:
    planned(session, vp, "Task")
    with pytest.raises(BadRequest, match="assignee is required"):
        claim(session, holder=" ")
    with pytest.raises(BadRequest, match="leaseSeconds must be between"):
        claim(session, lease_seconds=5)
    with pytest.raises(NotFound, match="Project not found"):
        claims.claim_task(session, ADMIN, "NOPE", RUNNER)


def test_tokens_claim_only_in_their_projects(session: Session, vp) -> None:
    other = projects.create_project(session, "OT", "Other")
    theirs = planned(session, other, "Foreign")
    planned(session, vp, "Mine")
    token = token_access("token-1", [vp.id])

    with pytest.raises(Forbidden, match="not allowed to access project"):
        claims.claim_task(session, token, "OT", RUNNER)
    with pytest.raises(NotFound):
        claims.claim_task(session, token, "VP", RUNNER, task=theirs.id)
    assert claims.claim_task(session, token, "VP", RUNNER).item.task.key == "VP-1"


# --- two runners at once -------------------------------------------------------------


def test_concurrent_claims_never_hand_out_the_same_task(db: Engine, session: Session, vp) -> None:
    for number in range(1, 7):
        planned(session, vp, f"Task {number}")
    runners = 10
    barrier = threading.Barrier(runners)
    results: list[str | None] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run(index: int) -> None:
        try:
            with Session(db, expire_on_commit=False) as own:
                barrier.wait(timeout=10)
                result = claims.claim_task(own, ADMIN, "VP", f"Runner {index}")
            with lock:
                results.append(result.item.card.id if result.claimed else None)
        except BaseException as error:  # noqa: BLE001 - surfaced by the assert below
            errors.append(error)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(runners)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    won = [card_id for card_id in results if card_id]
    assert len(won) == 6
    assert len(set(won)) == 6
    assert results.count(None) == runners - 6
    in_progress = board.board_columns(session, ADMIN, vp.id).in_progress
    assert len(in_progress) == 6
    assert len({card.assignee for card in in_progress}) == 6


def test_concurrent_claims_of_one_named_task_have_one_winner(
    db: Engine, session: Session, vp
) -> None:
    task = planned(session, vp, "Contended")
    runners = 6
    barrier = threading.Barrier(runners)
    outcomes: list[str] = []
    lock = threading.Lock()

    def run(index: int) -> None:
        with Session(db, expire_on_commit=False) as own:
            barrier.wait(timeout=10)
            try:
                claims.claim_task(own, ADMIN, "VP", f"Runner {index}", task=task.id)
                outcome = "won"
            except Conflict:
                outcome = "refused"
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(runners)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["refused"] * (runners - 1) + ["won"]
    assert kinds(session, task) == ["claimed"]


# --- reporting back ------------------------------------------------------------------


def test_hands_a_task_over_to_review_with_its_branch(session: Session, vp) -> None:
    task = planned(session, vp, "Implement")
    claim(session)

    handed = claims.hand_over_task(
        session, ADMIN, "VP-1", RUNNER, branch_name="issue-12", note="All tests pass"
    )

    assert handed.column == BoardColumn.REVIEW
    assert handed.branch_name == "issue-12"
    assert handed.claimed_at is None and handed.claim_expires_at is None
    assert handed.assignee is None
    assert ideas.get_idea(session, ADMIN, task.id).assignee is None
    events = history.list_task_history(session, ADMIN, task.id)
    assert events[0].kind == "handed_over"
    assert events[0].message == "Handed over to review on branch issue-12: All tests pass"
    assert events[0].actor == RUNNER


def test_only_the_holder_reports_back(session: Session, vp) -> None:
    planned(session, vp, "Held")
    planned(session, vp, "Free")
    claim(session)

    with pytest.raises(Conflict, match=f"VP-1 is not claimed by {OTHER}: {RUNNER} holds it"):
        claims.hand_over_task(session, ADMIN, "VP-1", OTHER, branch_name="x")
    with pytest.raises(Conflict, match=f"VP-2 is not claimed by {RUNNER}: it is not claimed"):
        claims.release_task(session, ADMIN, "VP-2", RUNNER)
    with pytest.raises(Conflict, match="is not claimed by"):
        claims.renew_claim(session, ADMIN, "VP-1", OTHER)


def test_returns_a_failed_task_to_planned_with_a_note(session: Session, vp) -> None:
    task = planned(session, vp, "Flaky")
    claim(session)

    returned = claims.release_task(session, ADMIN, "VP-1", RUNNER, note="Tests failed")

    assert returned.column == BoardColumn.PLANNED
    assert returned.attempts == 1
    assert returned.blocked_at is None
    assert returned.assignee is None
    assert history.list_task_history(session, ADMIN, task.id)[0].message == (
        "Attempt 1 of 3 failed: Tests failed"
    )
    # Back in the pool.
    assert claimed_key(claim(session, OTHER)) == "VP-1"


def test_blocks_a_task_after_too_many_failed_attempts(session: Session, vp) -> None:
    task = planned(session, vp, "Hopeless")
    for attempt in range(1, 3):
        claim(session)
        released = claims.release_task(
            session, ADMIN, task.id, RUNNER, note=f"Failure {attempt}", max_attempts=2
        )

    assert released.column == BoardColumn.PLANNED
    assert released.attempts == 2
    assert released.blocked_at is not None
    assert released.blocked_reason == "Failed 2 attempts: Failure 2"
    assert claim(session).claimed is False
    assert kinds(session, task)[:2] == ["blocked", "claimed"]


def test_blocks_a_task_with_a_note_and_requires_the_reason(session: Session, vp) -> None:
    task = planned(session, vp, "Unclear")
    claim(session)

    with pytest.raises(BadRequest, match="A note is required to block a task"):
        claims.release_task(session, ADMIN, task.id, RUNNER, ReleaseOutcome.BLOCKED)
    blocked = claims.release_task(
        session, ADMIN, task.id, RUNNER, ReleaseOutcome.BLOCKED, "Which database?"
    )

    assert blocked.column == BoardColumn.PLANNED
    assert blocked.blocked_reason == "Which database?"
    assert blocked.attempts == 0
    with pytest.raises(Conflict, match="it is blocked: Which database?"):
        claim(session, task=task.id)


def test_releases_a_task_without_counting_an_attempt(session: Session, vp) -> None:
    task = planned(session, vp, "Interrupted")
    claim(session)

    released = claims.release_task(
        session, ADMIN, task.id, RUNNER, ReleaseOutcome.RELEASED, "Usage limit reached"
    )

    assert (released.column, released.attempts, released.blocked_at) == (
        BoardColumn.PLANNED,
        0,
        None,
    )
    assert history.list_task_history(session, ADMIN, task.id)[0].message == (
        "Released: Usage limit reached"
    )


def test_moving_a_blocked_card_to_planned_unblocks_it_and_resets_attempts(
    session: Session, vp
) -> None:
    task = planned(session, vp, "Stuck")
    for _ in range(3):
        claim(session)
        claims.release_task(session, ADMIN, task.id, RUNNER, note="Nope")
    assert card(session, task).blocked_at is not None

    # The card already sits in Planned; writing Planned again is the unblock.
    unblocked = board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED)

    assert unblocked.blocked_at is None and unblocked.blocked_reason is None
    assert unblocked.attempts == 0
    assert kinds(session, task)[0] == "unblocked"
    assert claimed_key(claim(session)) == "VP-1"


def test_leaving_planned_drops_the_blocked_state(session: Session, vp) -> None:
    task = planned(session, vp, "Parked")
    claim(session)
    claims.release_task(session, ADMIN, task.id, RUNNER, ReleaseOutcome.BLOCKED, "Later")

    moved = board.update_card(session, ADMIN, task.id, column=BoardColumn.READY)

    assert moved.blocked_at is None
    assert moved.blocked_reason is None


def test_moving_a_claimed_card_by_hand_ends_the_claim(session: Session, vp) -> None:
    task = planned(session, vp, "Taken back")
    claim(session)

    moved = board.update_card(session, ADMIN, task.id, column=BoardColumn.REVIEW)

    assert moved.claimed_at is None
    assert moved.assignee is None
    assert ideas.get_idea(session, ADMIN, task.id).assignee is None
    event = history.list_task_history(session, ADMIN, task.id)[0]
    assert (event.kind, event.actor) == ("claim_ended", "admin")
    assert event.message == f"Claim by {RUNNER} ended: moved to review"
    with pytest.raises(Conflict, match="it is not claimed"):
        claims.hand_over_task(session, ADMIN, task.id, RUNNER)


def test_naming_another_holder_ends_the_claim_but_keeps_the_card(session: Session, vp) -> None:
    task = planned(session, vp, "Reassigned")
    claim(session)

    ideas.update_idea(session, ADMIN, task.id, assignee="Alice")

    reassigned = card(session, task)
    assert reassigned.column == BoardColumn.IN_PROGRESS
    assert reassigned.assignee == "Alice"
    assert reassigned.claimed_at is None
    assert kinds(session, task)[0] == "claim_ended"


def test_other_card_edits_keep_the_claim(session: Session, vp) -> None:
    task = planned(session, vp, "Edited while claimed")
    claim(session)

    board.update_card(session, ADMIN, task.id, details="More context")
    ideas.update_idea(session, ADMIN, task.id, title="Renamed")

    assert card(session, task).claimed_at is not None
    assert claims.hand_over_task(session, ADMIN, task.id, RUNNER).column == BoardColumn.REVIEW


# --- leases --------------------------------------------------------------------------


def test_renewing_extends_the_lease_without_touching_updated_at(session: Session, vp) -> None:
    task = planned(session, vp, "Long running")
    claimed = claim(session, lease_seconds=60).item.card
    time.sleep(0.002)

    renewed = claims.renew_claim(session, ADMIN, task.id, RUNNER, lease_seconds=600)

    assert renewed.claim_expires_at > claimed.claim_expires_at
    assert renewed.updated_at == claimed.updated_at


def test_expired_claims_return_to_planned_and_count_an_attempt(session: Session, vp) -> None:
    task = planned(session, vp, "Abandoned")
    claimed = claim(session, lease_seconds=60).item.card

    assert claims.expire_claims(session, timestamp=claimed.claim_expires_at) == 1

    expired = card(session, task)
    assert expired.column == BoardColumn.PLANNED
    assert (expired.claimed_at, expired.assignee, expired.attempts) == (None, None, 1)
    event = history.list_task_history(session, ADMIN, task.id)[0]
    assert event.kind == "expired"
    assert event.message == f"Claim by {RUNNER} expired without a report (attempt 1 of 3)"
    # The runner that vanished cannot report back any more.
    with pytest.raises(Conflict):
        claims.hand_over_task(session, ADMIN, task.id, RUNNER)


def test_live_claims_do_not_expire(session: Session, vp) -> None:
    planned(session, vp, "Busy")
    claimed = claim(session, lease_seconds=60).item.card

    assert claims.expire_claims(session, timestamp=claimed.claimed_at + timedelta(seconds=59)) == 0
    assert card(session, claimed).column == BoardColumn.IN_PROGRESS


def test_repeatedly_expiring_claims_block_the_task(session: Session, vp) -> None:
    task = planned(session, vp, "Crashes the runner")
    for _ in range(2):
        claimed = claim(session, lease_seconds=60).item.card
        claims.expire_claims(session, max_attempts=2, timestamp=claimed.claim_expires_at)

    blocked = card(session, task)
    assert blocked.blocked_reason == f"Failed 2 attempts: the claim by {RUNNER} expired"
    assert claim(session).claimed is False


def test_a_claim_sweeps_lapsed_claims_of_its_project_first(
    db: Engine, session: Session, vp
) -> None:
    task = planned(session, vp, "Lapsed")
    claim(session)
    with db.begin() as connection:
        connection.execute(
            text("update board_cards set claim_expires_at = now() - interval '1 minute'")
        )

    assert claimed_key(claim(session, OTHER)) == "VP-1"
    assert card(session, task).assignee == OTHER
    assert card(session, task).attempts == 1


def test_a_lapsed_claim_cannot_be_used_before_the_sweep_ends_it(
    db: Engine, session: Session, vp
) -> None:
    task = planned(session, vp, "Lapsed but not swept")
    claim(session)
    with db.begin() as connection:
        connection.execute(
            text("update board_cards set claim_expires_at = now() - interval '1 minute'")
        )

    for report in (
        lambda: claims.renew_claim(session, ADMIN, task.id, RUNNER),
        lambda: claims.hand_over_task(session, ADMIN, task.id, RUNNER, branch_name="late"),
        lambda: claims.release_task(session, ADMIN, task.id, RUNNER, ReleaseOutcome.RELEASED),
    ):
        with pytest.raises(Conflict, match=f"not claimed by {RUNNER}: the claim expired"):
            report()
    assert card(session, task).column == BoardColumn.IN_PROGRESS
    assert claimed_key(claim(session, OTHER)) == "VP-1"


def test_the_app_sweeps_expired_claims_in_the_background(
    db: Engine, session: Session, vp, settings
) -> None:
    from dataclasses import replace

    from vibepod_board.main import create_app

    task = planned(session, vp, "Swept")
    claim(session)
    with db.begin() as connection:
        connection.execute(
            text("update board_cards set claim_expires_at = now() - interval '1 minute'")
        )

    app = create_app(replace(settings, claim_sweep_seconds=1), run_migrations=False)
    with TestClient(app):
        deadline = time.monotonic() + 10
        while card(session, task).column != BoardColumn.PLANNED:
            assert time.monotonic() < deadline, "the sweep did not run"
            time.sleep(0.1)
    assert card(session, task).attempts == 1


# --- export --------------------------------------------------------------------------


def test_exports_leave_automation_state_behind(session: Session, vp) -> None:
    planned(session, vp, "Claimed")
    blocked = planned(session, vp, "Blocked")
    claim(session, task="VP-1")
    claim(session, task=blocked.id)
    claims.release_task(session, ADMIN, blocked.id, RUNNER, ReleaseOutcome.BLOCKED, "Why?")

    bundle = transfer.export_project(session, vp.id)
    cards = bundle.model_dump(mode="json")["boardCards"]

    assert parse_project_bundle(bundle.model_dump(mode="json"))
    assert all("claimedAt" not in card and "blockedReason" not in card for card in cards)
    assert all("attempts" not in card for card in cards)


# --- REST ----------------------------------------------------------------------------


@pytest.fixture
def api(client: TestClient) -> TestClient:
    login(client)
    assert client.post("/api/projects", json={"key": "VP", "title": "VibePod"}).status_code == 201
    return client


def rest_planned(client: TestClient, title: str, **fields: Any) -> dict[str, Any]:
    task = client.post("/api/ideas", json={"projectId": "VP", "title": title, **fields}).json()
    client.post(f"/api/ideas/{task['item']['id']}/ready")
    client.patch(f"/api/board/{task['item']['id']}", json={"column": "planned"})
    return task["item"]


def test_rest_runs_the_claim_lifecycle(api: TestClient) -> None:
    rest_planned(api, "Over REST")

    claimed = api.post("/api/board/claim", json={"projectId": "VP", "assignee": RUNNER})
    assert claimed.status_code == 200, claimed.text
    body = claimed.json()
    assert body["claimed"] is True
    assert body["item"]["task"]["key"] == "VP-1"
    assert body["item"]["card"]["column"] == "in_progress"
    assert body["item"]["card"]["assignee"] == RUNNER
    assert body["item"]["card"]["claimedAt"]

    renewed = api.post("/api/board/VP-1/renew", json={"assignee": RUNNER, "leaseSeconds": 120})
    assert renewed.status_code == 200
    assert renewed.json()["item"]["claimExpiresAt"]

    refused = api.post("/api/board/VP-1/handover", json={"assignee": OTHER})
    assert refused.status_code == 409

    handed = api.post(
        "/api/board/VP-1/handover", json={"assignee": RUNNER, "branchName": "vp-1", "note": "Done"}
    )
    assert handed.status_code == 200
    assert handed.json()["item"]["column"] == "review"
    assert handed.json()["item"]["branchName"] == "vp-1"

    events = api.get("/api/ideas/VP-1/history").json()["items"]
    assert [event["kind"] for event in events] == ["handed_over", "claimed"]

    nothing = api.post("/api/board/claim", json={"projectId": "VP", "assignee": RUNNER})
    assert nothing.json() == {"claimed": False, "reason": claims.NOTHING_TO_CLAIM}


def test_rest_releases_and_blocks(api: TestClient) -> None:
    rest_planned(api, "Fails")
    api.post("/api/board/claim", json={"projectId": "VP", "assignee": RUNNER})

    released = api.post(
        "/api/board/VP-1/release",
        json={"assignee": RUNNER, "outcome": "failed", "note": "Broken", "maxAttempts": 1},
    )
    assert released.status_code == 200
    card = released.json()["item"]
    assert (card["column"], card["attempts"], card["blockedReason"]) == (
        "planned",
        1,
        "Failed 1 attempt: Broken",
    )

    unblocked = api.patch("/api/board/VP-1", json={"column": "planned"}).json()["item"]
    assert "blockedAt" not in unblocked and unblocked["attempts"] == 0

    assert (
        api.post(
            "/api/board/VP-1/release", json={"assignee": RUNNER, "outcome": "sideways"}
        ).status_code
        == 400
    )
    assert (
        api.post(
            "/api/board/claim", json={"projectId": "VP", "assignee": RUNNER, "leaseSeconds": 1}
        ).status_code
        == 400
    )


def test_rest_uses_the_configured_lease(api: TestClient) -> None:
    rest_planned(api, "Default lease")
    body = api.post("/api/board/claim", json={"projectId": "VP", "assignee": RUNNER}).json()
    card = body["item"]["card"]
    from datetime import datetime

    claimed_at = datetime.fromisoformat(card["claimedAt"])
    expires_at = datetime.fromisoformat(card["claimExpiresAt"])
    assert expires_at - claimed_at == timedelta(seconds=claims.DEFAULT_LEASE_SECONDS)


def test_rest_scopes_claims_to_the_token_projects(api: TestClient) -> None:
    api.post("/api/projects", json={"key": "OT", "title": "Other"})
    rest_planned(api, "Mine")
    project_id = api.get("/api/projects").json()["items"]
    vp_id = next(project["id"] for project in project_id if project["key"] == "VP")
    token = api.post("/api/tokens", json={"name": "Runner", "projectIds": [vp_id]}).json()["token"]
    api.post("/api/auth/logout")
    headers = {"Authorization": f"Bearer {token}"}

    foreign = api.post(
        "/api/board/claim", json={"projectId": "OT", "assignee": RUNNER}, headers=headers
    )
    assert foreign.status_code == 403
    mine = api.post(
        "/api/board/claim", json={"projectId": "VP", "assignee": RUNNER}, headers=headers
    )
    assert mine.json()["claimed"] is True
    events = api.get("/api/ideas/VP-1/history", headers=headers).json()["items"]
    assert events[0]["actor"] == RUNNER


# --- MCP -----------------------------------------------------------------------------


@pytest.fixture
def token(session: Session, vp) -> str:
    projects.create_project(session, "OT", "Other")
    planned(session, vp, "Over MCP")
    return tokens_create(session, vp.id)


def tokens_create(session: Session, project_id: str) -> str:
    from vibepod_board.services import tokens

    return tokens.create_token(session, "Runner", [project_id]).token


async def test_mcp_runs_the_claim_lifecycle(server_url: str, token: str) -> None:
    claimed = await call(server_url, token, "claim_next_task", projectId="VP", assignee=RUNNER)
    assert claimed["claimed"] is True
    assert claimed["item"]["task"]["key"] == "VP-1"

    renewed = await call(server_url, token, "renew_task_claim", id="VP-1", assignee=RUNNER)
    assert renewed["item"]["claimedAt"]

    released = await call(
        server_url,
        token,
        "release_task",
        id="VP-1",
        assignee=RUNNER,
        outcome="blocked",
        note="Needs a decision",
    )
    assert released["item"]["blockedReason"] == "Needs a decision"
    assert released["item"]["column"] == "planned"

    events = await call(server_url, token, "list_task_history", id="VP-1")
    assert [event["kind"] for event in events["items"]] == ["blocked", "claimed"]

    nothing = await call(server_url, token, "claim_next_task", projectId="VP", assignee=RUNNER)
    assert nothing == {"claimed": False, "reason": claims.NOTHING_TO_CLAIM}

    with pytest.raises(ToolError, match="Token is not allowed to access project"):
        await call(server_url, token, "claim_next_task", projectId="OT", assignee=RUNNER)


async def test_mcp_hands_over(server_url: str, token: str) -> None:
    await call(server_url, token, "claim_next_task", projectId="VP", assignee=RUNNER)
    with pytest.raises(ToolError, match="is not claimed by"):
        await call(server_url, token, "hand_over_task", id="VP-1", assignee=OTHER)
    handed = await call(
        server_url, token, "hand_over_task", id="VP-1", assignee=RUNNER, branchName="vp-1"
    )
    assert handed["item"]["column"] == "review"
    assert handed["item"]["branchName"] == "vp-1"


# --- races and edges found in review -------------------------------------------------


def test_a_sync_from_a_copy_read_before_the_claim_keeps_it(
    db: Engine, session: Session, vp
) -> None:
    from vibepod_board.tables import IdeaRow

    task = planned(session, vp, "Pulled from GitHub meanwhile")
    with Session(db, expire_on_commit=False) as stale:
        # Read before the claim, like a GitHub pull waiting on the network.
        copy = stale.get(IdeaRow, task.id)
        claim(session)
        board.sync_card_from_idea(stale, copy, now())
        stale.commit()

    card = board.get_card(session, ADMIN, task.id)
    assert (card.assignee, card.claimed_at is not None) == (RUNNER, True)
    assert ideas.get_idea(session, ADMIN, task.id).assignee == RUNNER
    assert claims.hand_over_task(session, ADMIN, task.id, RUNNER).column == BoardColumn.REVIEW


def _race(db: Engine, *writes) -> list[BaseException]:
    """Runs the writes at the same time, each in its own session; a refused write (409) is
    fine, anything else is returned."""
    barrier = threading.Barrier(len(writes))
    errors: list[BaseException] = []

    def run(write) -> None:
        with Session(db, expire_on_commit=False) as own:
            barrier.wait(timeout=10)
            try:
                write(own)
            except Conflict:
                pass
            except BaseException as error:  # noqa: BLE001 - returned to the test
                errors.append(error)

    threads = [threading.Thread(target=run, args=(write,)) for write in writes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return errors


def test_moving_a_claimed_card_while_it_is_handed_over_never_deadlocks(
    db: Engine, session: Session, vp
) -> None:
    for round_number in range(8):
        task = planned(session, vp, f"Contended {round_number}")
        claim(session, task=task.id)
        errors = _race(
            db,
            lambda own, key=task.id: board.update_card(own, ADMIN, key, column=BoardColumn.DONE),
            lambda own, key=task.id: claims.hand_over_task(own, ADMIN, key, RUNNER),
        )
        assert errors == []


def test_taking_a_claimed_task_off_the_board_frees_its_holder(session: Session, vp) -> None:
    task = planned(session, vp, "Taken off")
    claim(session)

    ideas.set_board_availability(session, ADMIN, task.id, False)

    assert ideas.get_idea(session, ADMIN, task.id).assignee is None
    assert kinds(session, task)[0] == "claim_ended"
    ideas.mark_ready(session, ADMIN, task.id)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED)
    assert claimed_key(claim(session, OTHER)) == "VP-1"


def test_a_lease_setting_out_of_range_fails_at_startup(monkeypatch) -> None:
    from vibepod_board.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgres://x@localhost/x")
    monkeypatch.setenv("CLAIM_LEASE_SECONDS", "5")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="CLAIM_LEASE_SECONDS must be between 30"):
            get_settings()
        monkeypatch.setenv("CLAIM_LEASE_SECONDS", "120")
        get_settings.cache_clear()
        assert get_settings().claim_lease_seconds == 120
    finally:
        get_settings.cache_clear()
