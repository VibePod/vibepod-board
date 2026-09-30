"""Review workers: review claims on tasks in Review, verdicts bound to the head commit,
approvals against the project's requirement, rework, and the loop guard."""

import threading
from datetime import timedelta
from typing import Any

import pytest
from conftest import call, login
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine, text
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.enums import (
    BoardColumn,
    ClaimMode,
    InstructionType,
    ReviewVerdict,
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
    reviews,
    tokens,
    transfer,
    workers,
)

ADMIN = admin_access("admin")
RUNNER = "Claude::Runner"
CLAUDE = "Claude::Reviewer"
CODEX = "Codex::Reviewer"
THIRD = "Gemini::Reviewer"
SHA = "a" * 40
NEXT_SHA = "b" * 40


@pytest.fixture
def vp(session: Session):
    return projects.create_project(session, "VP", "VibePod")


def in_review(session: Session, project, title: str, sha: str | None = SHA, branch="vp-1"):
    """A task an implementation run handed over to Review at `sha`."""
    task = ideas.create_idea(session, ADMIN, title=title, project_id=project.id)
    ideas.mark_ready(session, ADMIN, task.id)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED)
    claims.claim_task(session, ADMIN, project.key, RUNNER, task=task.id)
    claims.hand_over_task(session, ADMIN, task.id, RUNNER, branch_name=branch, head_sha=sha)
    return task


def hand_over_again(session: Session, task, sha: str) -> None:
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED)
    claims.claim_task(session, ADMIN, "VP", RUNNER, task=task.id)
    claims.hand_over_task(session, ADMIN, task.id, RUNNER, branch_name="vp-1", head_sha=sha)


def review(session: Session, reviewer: str = CLAUDE, **options: Any):
    return claims.claim_task(session, ADMIN, "VP", reviewer, mode=ClaimMode.REVIEW, **options)


def verdict(session: Session, task, reviewer: str, kind: ReviewVerdict, sha=SHA, note=None):
    return reviews.submit_review(session, ADMIN, task.id, reviewer, kind, sha, note)


def card(session: Session, task):
    return board.get_card(session, ADMIN, task.id)


def kinds(session: Session, task) -> list[str]:
    return [event.kind for event in history.list_task_history(session, ADMIN, task.id)]


def require(session: Session, approvals: int = 1, rounds: int | None = None) -> None:
    projects.update_project_settings(session, ADMIN, "VP", approvals, rounds)


# --- settings ------------------------------------------------------------------------


def test_projects_require_one_approval_and_three_rounds_by_default(session: Session, vp) -> None:
    assert (vp.required_approvals, vp.max_review_rounds) == (1, 3)

    updated = projects.update_project_settings(session, ADMIN, "VP", 3, 5)

    assert (updated.required_approvals, updated.max_review_rounds) == (3, 5)
    for approvals, rounds in ((0, None), (6, None), (None, 0), (None, 11)):
        with pytest.raises(BadRequest):
            projects.update_project_settings(session, ADMIN, "VP", approvals, rounds)


def test_settings_over_rest(client: TestClient, session: Session, vp) -> None:
    token = tokens.create_token(session, "Reviewer", [vp.id]).token
    headers = {"Authorization": f"Bearer {token}"}

    response = client.patch(
        f"/api/projects/{vp.id}/settings", json={"requiredApprovals": 2}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["item"]["requiredApprovals"] == 2
    assert response.json()["item"]["maxReviewRounds"] == 3
    refused = client.patch(
        f"/api/projects/{vp.id}/settings", json={"requiredApprovals": 6}, headers=headers
    )
    assert refused.status_code == 400

    login(client)
    edited = client.patch(f"/api/projects/{vp.id}", json={"maxReviewRounds": 4})
    assert edited.json()["item"]["maxReviewRounds"] == 4
    created = client.post(
        "/api/projects", json={"key": "NEW", "title": "New", "requiredApprovals": 5}
    )
    assert created.json()["item"]["requiredApprovals"] == 5
    listed = {item["key"]: item for item in client.get("/api/projects").json()["items"]}
    assert listed["VP"]["requiredApprovals"] == 2


def test_settings_stay_out_of_the_export(session: Session, vp) -> None:
    require(session, 2)
    in_review(session, vp, "Exported mid-review")
    review(session)

    bundle = transfer.export_project(session, vp.id)

    assert "requiredApprovals" not in bundle.project.model_dump()
    assert bundle.board_cards[0].column == BoardColumn.REVIEW


# --- review claims -------------------------------------------------------------------


def test_a_review_claim_keeps_the_card_in_review_and_names_the_head(session: Session, vp) -> None:
    task = in_review(session, vp, "Reviewable")
    before = card(session, task)

    result = review(session, lease_seconds=600)

    assert result.claimed
    assert result.review.head_sha == SHA
    assert result.review.reviewer == CLAUDE
    assert result.review.open
    assert result.item.task.key == "VP-1"
    assert result.item.card.head_sha == SHA
    after = card(session, task)
    assert after.column == BoardColumn.REVIEW
    assert after.assignee is None and after.claimed_at is None
    assert after.attempts == before.attempts == 0
    assert kinds(session, task)[0] == TaskEventKind.REVIEW_STARTED
    started = history.list_task_history(session, ADMIN, task.id)[0]
    assert started.actor == CLAUDE and SHA[:12] in started.message


def test_review_claims_skip_cards_without_a_branch_and_blocked_cards(session: Session, vp) -> None:
    no_branch = in_review(session, vp, "No branch", branch=None)
    blocked = in_review(session, vp, "Blocked")
    review(session, THIRD)
    verdict(session, blocked, THIRD, ReviewVerdict.NEEDS_INPUT, note="Which API?")

    assert review(session).claimed is False
    with pytest.raises(Conflict, match="names no branch"):
        review(session, task=no_branch.id)
    with pytest.raises(Conflict, match="is blocked"):
        review(session, task=blocked.id)


def test_implementation_claims_never_take_a_task_in_review(session: Session, vp) -> None:
    task = in_review(session, vp, "Waiting for reviewers")

    assert claims.claim_task(session, ADMIN, "VP", "Other runner").claimed is False
    with pytest.raises(Conflict, match="in review, not planned"):
        claims.claim_task(session, ADMIN, "VP", "Other runner", task=task.id)


def test_review_claims_follow_the_work_order_and_honour_exclude(session: Session, vp) -> None:
    first = in_review(session, vp, "First")
    in_review(session, vp, "Second")

    assert review(session).item.task.key == "VP-1"
    assert review(session, CODEX, exclude=[first.id]).item.task.key == "VP-2"


def test_asking_again_for_a_task_under_review_renews_it(session: Session, vp) -> None:
    task = in_review(session, vp, "Restarted reviewer")
    first = review(session, lease_seconds=60).review

    again = review(session, task=task.id, lease_seconds=3600).review

    assert again.id == first.id
    assert again.lease_expires_at > first.lease_expires_at
    assert kinds(session, task).count(TaskEventKind.REVIEW_STARTED) == 1


def test_several_reviewers_review_one_task_up_to_the_requirement(session: Session, vp) -> None:
    require(session, 2)
    task = in_review(session, vp, "Two opinions")

    assert review(session, CLAUDE).claimed
    assert review(session, CODEX).claimed
    # Two open reviews already cover the two approvals needed.
    third = review(session, THIRD)
    assert third.claimed is False
    with pytest.raises(Conflict, match="0 approvals and 2 open reviews of the 2"):
        review(session, THIRD, task=task.id)

    state = reviews.review_state(session, ADMIN, task.id)
    assert state.open_reviews == 2
    assert {item.reviewer for item in state.items} == {CLAUDE, CODEX}


def test_a_reviewer_never_reviews_the_same_head_twice(session: Session, vp) -> None:
    require(session, 2)
    task = in_review(session, vp, "Once per commit")
    review(session)
    verdict(session, task, CLAUDE, ReviewVerdict.APPROVE)

    assert review(session).claimed is False
    with pytest.raises(Conflict, match="already reviewed"):
        review(session, task=task.id)
    assert review(session, CODEX).claimed


def test_a_released_review_may_be_taken_again(session: Session, vp) -> None:
    task = in_review(session, vp, "Released")
    review(session)
    verdict(session, task, CLAUDE, ReviewVerdict.RELEASED, sha=None, note="Shutting down")

    assert review(session).claimed
    assert card(session, task).column == BoardColumn.REVIEW


def test_concurrent_reviewers_never_exceed_the_requirement(
    db: Engine, session: Session, vp
) -> None:
    require(session, 2)
    in_review(session, vp, "Contended review")
    reviewers = 8
    barrier = threading.Barrier(reviewers)
    results: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run(index: int) -> None:
        try:
            with Session(db, expire_on_commit=False) as own:
                barrier.wait(timeout=10)
                result = claims.claim_task(
                    own, ADMIN, "VP", f"Reviewer {index}", mode=ClaimMode.REVIEW
                )
            with lock:
                results.append(result.claimed)
        except BaseException as error:  # noqa: BLE001 - surfaced by the assert below
            errors.append(error)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(reviewers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert results.count(True) == 2
    task = ideas.get_idea(session, ADMIN, "VP-1")
    assert reviews.review_state(session, ADMIN, task.id).open_reviews == 2


def test_concurrent_approvals_move_the_task_once(db: Engine, session: Session, vp) -> None:
    require(session, 2)
    task = in_review(session, vp, "Approved at once")
    review(session, CLAUDE)
    review(session, CODEX)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run(reviewer: str) -> None:
        try:
            with Session(db, expire_on_commit=False) as own:
                barrier.wait(timeout=10)
                reviews.submit_review(own, ADMIN, task.id, reviewer, ReviewVerdict.APPROVE, SHA)
        except BaseException as error:  # noqa: BLE001 - surfaced by the assert below
            errors.append(error)

    threads = [threading.Thread(target=run, args=(name,)) for name in (CLAUDE, CODEX)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert card(session, task).column == BoardColumn.PR_READY
    assert kinds(session, task).count(TaskEventKind.APPROVED) == 2


# --- verdicts ------------------------------------------------------------------------


def test_the_task_moves_to_pr_ready_with_enough_approvals_of_its_head(session: Session, vp) -> None:
    require(session, 2)
    task = in_review(session, vp, "Needs two")
    review(session, CLAUDE)
    review(session, CODEX)

    first = verdict(session, task, CLAUDE, ReviewVerdict.APPROVE, note="Looks right")
    assert (first.approvals, first.column) == (1, BoardColumn.REVIEW)
    assert first.approved_by == [CLAUDE]

    second = verdict(session, task, CODEX, ReviewVerdict.APPROVE)
    assert (second.approvals, second.column) == (2, BoardColumn.PR_READY)
    assert second.approved_by == [CLAUDE, CODEX]
    assert second.open_reviews == 0

    approved = [
        event
        for event in history.list_task_history(session, ADMIN, task.id)
        if event.kind == TaskEventKind.APPROVED
    ]
    assert [event.actor for event in approved] == [CODEX, CLAUDE]
    assert all(SHA[:12] in event.message for event in approved)
    assert "Looks right" in approved[1].message


def test_only_the_holder_of_a_review_submits_its_verdict(session: Session, vp) -> None:
    task = in_review(session, vp, "Held by Claude")
    review(session, CLAUDE)

    with pytest.raises(Conflict, match="is not being reviewed by Codex"):
        verdict(session, task, CODEX, ReviewVerdict.APPROVE)
    with pytest.raises(Conflict, match="is not being reviewed by"):
        verdict(session, task, RUNNER, ReviewVerdict.REWORK, note="Nope")
    assert card(session, task).column == BoardColumn.REVIEW


def test_a_verdict_must_name_the_reviewed_head(session: Session, vp) -> None:
    task = in_review(session, vp, "Named head")
    review(session)

    with pytest.raises(BadRequest, match="headSha is required"):
        verdict(session, task, CLAUDE, ReviewVerdict.APPROVE, sha=None)
    with pytest.raises(Conflict, match="the branch moved on"):
        verdict(session, task, CLAUDE, ReviewVerdict.APPROVE, sha=NEXT_SHA)
    with pytest.raises(BadRequest, match="Feedback is required"):
        verdict(session, task, CLAUDE, ReviewVerdict.REWORK)
    # Upper case is the same commit.
    assert verdict(session, task, CLAUDE, ReviewVerdict.APPROVE, sha=SHA.upper()).approvals == 1


def test_rework_sends_the_task_back_and_ends_the_other_reviews(session: Session, vp) -> None:
    require(session, 2)
    task = in_review(session, vp, "Disputed")
    review(session, CLAUDE)
    review(session, CODEX)
    worker = workers.register_worker(session, ADMIN, "VP", CODEX, mode=ClaimMode.REVIEW).item
    workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)

    state = verdict(session, task, CLAUDE, ReviewVerdict.REWORK, note="Missing tests")

    assert state.column == BoardColumn.PLANNED
    assert state.open_reviews == 0
    moved = card(session, task)
    assert (moved.column, moved.branch_name, moved.review_rounds) == (
        BoardColumn.PLANNED,
        "vp-1",
        1,
    )
    events = history.list_task_history(session, ADMIN, task.id)
    feedback = next(event for event in events if event.kind == TaskEventKind.FEEDBACK)
    assert (feedback.message, feedback.actor) == ("Missing tests", CLAUDE)
    requested = next(event for event in events if event.kind == TaskEventKind.REWORK_REQUESTED)
    assert requested.actor == CLAUDE and SHA[:12] in requested.message
    # The other review is over: its verdict is refused and its worker told to cancel.
    with pytest.raises(Conflict, match="is not being reviewed by"):
        verdict(session, task, CODEX, ReviewVerdict.APPROVE)
    beat = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)
    assert [instruction.type for instruction in beat.instructions] == [InstructionType.CANCEL]
    # The next run continues on the branch.
    assert claims.claim_task(session, ADMIN, "VP", RUNNER).item.task.key == "VP-1"


def test_a_new_head_starts_the_approval_count_over(session: Session, vp) -> None:
    require(session, 2)
    task = in_review(session, vp, "Moved on")
    review(session, CLAUDE)
    review(session, CODEX)
    verdict(session, task, CLAUDE, ReviewVerdict.APPROVE)
    verdict(session, task, CODEX, ReviewVerdict.REWORK, note="One more thing")

    hand_over_again(session, task, NEXT_SHA)

    state = reviews.review_state(session, ADMIN, task.id)
    assert (state.head_sha, state.approvals) == (NEXT_SHA, 0)
    # Both may review the new commit, and the approval of the old one no longer counts.
    claimed = review(session, CLAUDE)
    assert claimed.review.head_sha == NEXT_SHA
    assert verdict(session, task, CLAUDE, ReviewVerdict.APPROVE, sha=NEXT_SHA).approvals == 1
    review(session, CODEX)
    with pytest.raises(Conflict, match="the branch moved on"):
        verdict(session, task, CODEX, ReviewVerdict.APPROVE, sha=SHA)
    final = verdict(session, task, CODEX, ReviewVerdict.APPROVE, sha=NEXT_SHA)
    assert (final.approvals, final.column) == (2, BoardColumn.PR_READY)


def test_hand_overs_without_a_head_bind_reviews_to_the_hand_over(session: Session, vp) -> None:
    task = in_review(session, vp, "No SHA", sha=None)
    claimed = review(session)
    assert claimed.review.head_sha is None
    verdict(session, task, CLAUDE, ReviewVerdict.REWORK, sha=None, note="Try again")

    hand_over_again(session, task, None)  # type: ignore[arg-type]

    assert review(session).claimed
    assert verdict(session, task, CLAUDE, ReviewVerdict.APPROVE, sha=None).column == (
        BoardColumn.PR_READY
    )


def test_a_reviewer_question_blocks_the_task_in_review(session: Session, vp) -> None:
    task = in_review(session, vp, "Unclear")
    review(session)

    state = verdict(session, task, CLAUDE, ReviewVerdict.NEEDS_INPUT, note="Is v1 still used?")

    blocked = card(session, task)
    assert (state.column, blocked.question) == (BoardColumn.REVIEW, "Is v1 still used?")
    assert blocked.blocked_at is not None
    assert review(session, CODEX).claimed is False

    answered = conversation.answer_question(session, ADMIN, task.id, "No")
    assert (answered.column, answered.blocked_at, answered.question) == (
        BoardColumn.REVIEW,
        None,
        None,
    )
    # The reviewer that asked gets to look again.
    assert review(session).claimed


def test_a_question_ends_the_other_reviews_and_blocks_verdicts(session: Session, vp) -> None:
    require(session, 3)
    task = in_review(session, vp, "Asked mid-review")
    review(session, CLAUDE)
    review(session, CODEX)
    review(session, THIRD)
    worker = workers.register_worker(session, ADMIN, "VP", CODEX, mode=ClaimMode.REVIEW).item

    state = verdict(session, task, CLAUDE, ReviewVerdict.NEEDS_INPUT, note="Which API?")

    assert state.open_reviews == 0
    with pytest.raises(Conflict, match="is not being reviewed by"):
        verdict(session, task, CODEX, ReviewVerdict.APPROVE)
    beat = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)
    assert [instruction.type for instruction in beat.instructions] == [InstructionType.CANCEL]
    assert card(session, task).question == "Which API?"


def test_a_blocked_task_takes_no_verdict(session: Session, vp) -> None:
    require(session, 2)
    task = in_review(session, vp, "Blocked while reviewed")
    review(session, CLAUDE)
    review(session, CODEX)
    # Blocked by any path that leaves reviews open, such as a question asked before.
    session.execute(
        text(
            "update board_cards set blocked_at = now(), blocked_reason = 'Hold' where idea_id = :id"
        ),
        {"id": task.id},
    )
    session.commit()

    for kind, note in (
        (ReviewVerdict.APPROVE, None),
        (ReviewVerdict.REWORK, "More"),
        (ReviewVerdict.NEEDS_INPUT, "Why?"),
    ):
        with pytest.raises(Conflict, match="is blocked until a human acts: Hold"):
            verdict(session, task, CLAUDE, kind, note=note)
    # Ending a review without judging the task is still allowed.
    assert verdict(session, task, CODEX, ReviewVerdict.RELEASED, sha=None).open_reviews == 1
    assert reviews.review_state(session, ADMIN, task.id).approvals == 0


def test_a_failed_review_counts_no_attempt(session: Session, vp) -> None:
    task = in_review(session, vp, "Reviewer crashed")
    review(session)

    verdict(session, task, CLAUDE, ReviewVerdict.FAILED, sha=None, note="Agent crashed")

    failed = card(session, task)
    assert (failed.column, failed.attempts) == (BoardColumn.REVIEW, 0)
    assert kinds(session, task)[0] == TaskEventKind.REVIEW_ENDED
    assert review(session).claimed is False
    assert review(session, CODEX).claimed


def test_the_loop_guard_blocks_after_consecutive_rework_rounds(session: Session, vp) -> None:
    require(session, 1, 2)
    task = in_review(session, vp, "Going in circles")
    review(session)
    verdict(session, task, CLAUDE, ReviewVerdict.REWORK, note="Round one")
    claims.claim_task(session, ADMIN, "VP", RUNNER)
    claims.hand_over_task(session, ADMIN, task.id, RUNNER, head_sha=NEXT_SHA)
    review(session)

    state = verdict(session, task, CLAUDE, ReviewVerdict.REWORK, sha=NEXT_SHA, note="Round two")

    blocked = card(session, task)
    assert state.column == BoardColumn.REVIEW
    assert blocked.review_rounds == 2
    assert blocked.blocked_reason == "Sent back for rework 2 times in a row: Round two"
    assert kinds(session, task)[:3] == [
        TaskEventKind.BLOCKED,
        TaskEventKind.FEEDBACK,
        TaskEventKind.REWORK_REQUESTED,
    ]
    assert review(session, CODEX).claimed is False
    assert claims.claim_task(session, ADMIN, "VP", RUNNER).claimed is False

    # Moving the card by hand resets the count.
    moved = board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED)
    assert (moved.review_rounds, moved.blocked_at) == (0, None)


def test_an_approval_to_pr_ready_resets_the_rounds(session: Session, vp) -> None:
    task = in_review(session, vp, "Eventually good")
    review(session)
    verdict(session, task, CLAUDE, ReviewVerdict.REWORK, note="Almost")
    claims.claim_task(session, ADMIN, "VP", RUNNER)
    claims.hand_over_task(session, ADMIN, task.id, RUNNER, head_sha=NEXT_SHA)
    assert card(session, task).review_rounds == 1
    review(session)

    verdict(session, task, CLAUDE, ReviewVerdict.APPROVE, sha=NEXT_SHA)

    assert card(session, task).review_rounds == 0


def test_lowering_the_requirement_promotes_cards_it_already_approved(session: Session, vp) -> None:
    require(session, 3)
    task = in_review(session, vp, "Approved enough")
    waiting = in_review(session, vp, "Approved once, then asked")
    review(session, CLAUDE, task=task.id)
    review(session, CODEX, task=task.id)
    review(session, THIRD, task=task.id)
    verdict(session, task, CLAUDE, ReviewVerdict.APPROVE)
    verdict(session, task, CODEX, ReviewVerdict.APPROVE)
    review(session, CLAUDE, task=waiting.id)
    review(session, CODEX, task=waiting.id)
    verdict(session, waiting, CLAUDE, ReviewVerdict.APPROVE)
    verdict(session, waiting, CODEX, ReviewVerdict.NEEDS_INPUT, note="Which API?")

    require(session, 1)

    state = reviews.review_state(session, ADMIN, task.id)
    assert (state.column, state.approvals, state.open_reviews) == (BoardColumn.PR_READY, 2, 0)
    assert card(session, task).review_rounds == 0
    assert kinds(session, task)[:2] == [TaskEventKind.REVIEW_ENDED, TaskEventKind.APPROVED]
    # The blocked card waits for its answer, and then moves on too.
    assert card(session, waiting).column == BoardColumn.REVIEW
    answered = conversation.answer_question(session, ADMIN, waiting.id, "The new one")
    assert (answered.column, answered.blocked_at) == (BoardColumn.PR_READY, None)


# --- lease, cancel and moves ---------------------------------------------------------


def test_an_expired_review_ends_without_a_verdict_or_attempt(session: Session, vp) -> None:
    task = in_review(session, vp, "Abandoned")
    claimed = review(session, lease_seconds=60).review

    assert claims.expire_claims(session, timestamp=claimed.lease_expires_at) == 1

    state = reviews.review_state(session, ADMIN, task.id)
    assert state.open_reviews == 0
    assert state.items[0].verdict is None
    assert "expired" in (state.items[0].ended_reason or "")
    expired = card(session, task)
    assert (expired.column, expired.attempts) == (BoardColumn.REVIEW, 0)
    with pytest.raises(Conflict, match="is not being reviewed"):
        verdict(session, task, CLAUDE, ReviewVerdict.APPROVE)
    # Expiry is no verdict: the reviewer may take the task again.
    assert review(session).claimed


def test_a_lapsed_review_is_refused_before_the_sweep(session: Session, vp) -> None:
    from vibepod_board.tables import TaskReviewRow

    task = in_review(session, vp, "Lapsed")
    claimed = review(session).review
    row = session.get(TaskReviewRow, claimed.id)
    row.lease_expires_at = now()
    session.commit()

    with pytest.raises(Conflict, match="the review expired"):
        verdict(session, task, CLAUDE, ReviewVerdict.APPROVE)


def test_reviews_are_renewed_by_heartbeats_and_released_on_sign_off(session: Session, vp) -> None:
    task = in_review(session, vp, "Watched")
    registered = workers.register_worker(session, ADMIN, "VP", CLAUDE, mode=ClaimMode.REVIEW)
    assert registered.item.mode == ClaimMode.REVIEW
    claimed = review(session, lease_seconds=60).review
    assert claimed.worker_id == registered.item.id

    workers.heartbeat(
        session, ADMIN, registered.item.id, WorkerStatus.WORKING, task=task.id, lease_seconds=3600
    )
    renewed = reviews.review_state(session, ADMIN, task.id).items[0]
    assert renewed.lease_expires_at > claimed.lease_expires_at
    assert claims.expire_claims(session, timestamp=claimed.lease_expires_at) == 0

    workers.sign_off(session, ADMIN, registered.item.id)

    ended = reviews.review_state(session, ADMIN, task.id).items[0]
    assert (ended.open, ended.verdict) == (False, ReviewVerdict.RELEASED)
    assert card(session, task).column == BoardColumn.REVIEW
    listed = workers.list_workers(session, ADMIN, "VP")
    assert [worker.mode for worker in listed] == [ClaimMode.REVIEW]


def test_a_review_is_cancelled_from_the_board(session: Session, vp) -> None:
    require(session, 2)
    task = in_review(session, vp, "Cancelled")
    first = review(session, CLAUDE).review
    review(session, CODEX)
    worker = workers.register_worker(session, ADMIN, "VP", CLAUDE, mode=ClaimMode.REVIEW).item
    workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)

    state = reviews.cancel_review(session, ADMIN, task.id, first.id, "Wrong model")

    assert state.open_reviews == 1
    assert card(session, task).column == BoardColumn.REVIEW
    beat = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)
    assert [instruction.type for instruction in beat.instructions] == [InstructionType.CANCEL]
    assert "Wrong model" in (beat.instructions[0].reason or "")
    with pytest.raises(Conflict, match="already ended"):
        reviews.cancel_review(session, ADMIN, task.id, first.id)

    # Cancelling the run of a card in Review ends the reviews left.
    claims.cancel_run(session, ADMIN, task.id)
    assert reviews.review_state(session, ADMIN, task.id).open_reviews == 0
    assert card(session, task).column == BoardColumn.REVIEW
    assert card(session, task).attempts == 0


def test_a_review_worker_on_its_review_gets_no_cancel(session: Session, vp) -> None:
    task = in_review(session, vp, "Under review")
    review(session, CLAUDE)
    worker = workers.register_worker(session, ADMIN, "VP", CLAUDE, mode=ClaimMode.REVIEW).item

    beat = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)

    assert beat.instructions == []


def test_a_review_worker_whose_review_lapsed_is_told_to_cancel(session: Session, vp) -> None:
    task = in_review(session, vp, "Review lapsed")
    opened = review(session, CLAUDE).review
    worker = workers.register_worker(session, ADMIN, "VP", CLAUDE, mode=ClaimMode.REVIEW).item
    session.execute(
        text("update task_reviews set lease_expires_at = :at where id = :id"),
        {"at": now() - timedelta(seconds=1), "id": opened.id},
    )
    session.commit()

    beat = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.WORKING, task=task.id)

    assert [instruction.type for instruction in beat.instructions] == [InstructionType.CANCEL]


def test_moving_a_card_by_hand_ends_its_reviews(session: Session, vp) -> None:
    task = in_review(session, vp, "Taken over")
    review(session)

    board.update_card(session, ADMIN, task.id, column=BoardColumn.DONE)

    assert reviews.review_state(session, ADMIN, task.id).open_reviews == 0
    assert kinds(session, task)[0] == TaskEventKind.REVIEW_ENDED
    with pytest.raises(Conflict):
        verdict(session, task, CLAUDE, ReviewVerdict.APPROVE)


def test_feedback_from_the_board_ends_the_reviews(session: Session, vp) -> None:
    task = in_review(session, vp, "Human says no")
    review(session)

    conversation.request_rework(session, ADMIN, task.id, "Rename it")

    assert reviews.review_state(session, ADMIN, task.id).open_reviews == 0
    assert card(session, task).column == BoardColumn.PLANNED


def test_pausing_automation_pauses_review_claims(session: Session, vp) -> None:
    in_review(session, vp, "Paused")
    worker = workers.register_worker(session, ADMIN, "VP", CLAUDE, mode=ClaimMode.REVIEW).item
    automation.pause_automation(session, ADMIN, "VP", "Budget")

    result = review(session)

    assert (result.claimed, result.paused) == (False, True)
    beat = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE)
    assert [instruction.type for instruction in beat.instructions] == [InstructionType.PAUSE]


# --- REST and MCP --------------------------------------------------------------------


def test_rest_runs_the_review_lifecycle(client: TestClient, session: Session, vp) -> None:
    require(session, 2)
    task = in_review(session, vp, "Over REST")
    token = tokens.create_token(session, "Reviewer", [vp.id]).token
    headers = {"Authorization": f"Bearer {token}"}

    registered = client.post(
        "/api/workers",
        json={"projectId": "VP", "name": CLAUDE, "mode": "review"},
        headers=headers,
    )
    assert registered.json()["item"]["mode"] == "review"

    claimed = client.post(
        "/api/board/claim",
        json={"projectId": "VP", "assignee": CLAUDE, "mode": "review"},
        headers=headers,
    ).json()
    assert claimed["claimed"] is True
    assert claimed["review"]["headSha"] == SHA
    assert claimed["item"]["card"]["column"] == "review"

    renewed = client.post(
        "/api/board/VP-1/review/renew",
        json={"assignee": CLAUDE, "leaseSeconds": 900},
        headers=headers,
    )
    assert renewed.status_code == 200, renewed.text

    stale = client.post(
        "/api/board/VP-1/review",
        json={"assignee": CLAUDE, "verdict": "approve", "headSha": NEXT_SHA},
        headers=headers,
    )
    assert stale.status_code == 409
    foreign = client.post(
        "/api/board/VP-1/review",
        json={"assignee": CODEX, "verdict": "approve", "headSha": SHA},
        headers=headers,
    )
    assert foreign.status_code == 409
    approved = client.post(
        "/api/board/VP-1/review",
        json={"assignee": CLAUDE, "verdict": "approve", "headSha": SHA, "note": "Fine"},
        headers=headers,
    ).json()
    assert (approved["approvals"], approved["requiredApprovals"]) == (1, 2)
    assert approved["column"] == "review"

    second = client.post(
        "/api/board/claim",
        json={"projectId": "VP", "assignee": CODEX, "mode": "review"},
        headers=headers,
    ).json()
    cancelled = client.post(
        f"/api/board/VP-1/reviews/{second['review']['id']}/cancel",
        json={"reason": "Not now"},
        headers=headers,
    ).json()
    assert cancelled["openReviews"] == 0

    state = client.get("/api/board/VP-1/reviews", headers=headers).json()
    assert state["approvedBy"] == [CLAUDE]
    assert [item["verdict"] for item in state["items"] if "verdict" in item] == ["approve"]
    assert state["items"][1]["feedback"] == "Fine"

    handed = client.post(
        "/api/board/claim", json={"projectId": "VP", "assignee": RUNNER}, headers=headers
    ).json()
    assert handed["claimed"] is False
    assert task.id


async def test_mcp_runs_the_review_lifecycle(server_url: str, session: Session, vp) -> None:
    in_review(session, vp, "Over MCP")
    token = tokens.create_token(session, "Reviewer", [vp.id]).token

    settings = await call(
        server_url, token, "update_project_settings", projectId="VP", requiredApprovals=2
    )
    assert settings["item"]["requiredApprovals"] == 2

    registered = await call(
        server_url, token, "register_worker", projectId="VP", name=CLAUDE, mode="review"
    )
    assert registered["item"]["mode"] == "review"
    claimed = await call(
        server_url, token, "claim_next_task", projectId="VP", assignee=CLAUDE, mode="review"
    )
    assert claimed["review"]["headSha"] == SHA
    await call(server_url, token, "renew_task_review", id="VP-1", assignee=CLAUDE)

    with pytest.raises(ToolError, match="the branch moved on"):
        await call(
            server_url,
            token,
            "submit_review",
            id="VP-1",
            assignee=CLAUDE,
            verdict="approve",
            headSha=NEXT_SHA,
        )
    approved = await call(
        server_url,
        token,
        "submit_review",
        id="VP-1",
        assignee=CLAUDE,
        verdict="approve",
        headSha=SHA,
    )
    assert approved["approvals"] == 1

    other = await call(
        server_url, token, "claim_next_task", projectId="VP", assignee=CODEX, mode="review"
    )
    cancelled = await call(
        server_url, token, "cancel_task_review", id="VP-1", reviewId=other["review"]["id"]
    )
    assert cancelled["openReviews"] == 0

    await call(server_url, token, "claim_next_task", projectId="VP", assignee=THIRD, mode="review")
    rework = await call(
        server_url,
        token,
        "submit_review",
        id="VP-1",
        assignee=THIRD,
        verdict="rework",
        headSha=SHA,
        note="Handle errors",
    )
    assert rework["column"] == "planned"

    state = await call(server_url, token, "list_task_reviews", id="VP-1")
    assert state["reviewRounds"] == 1
    history_kinds = [
        event["kind"]
        for event in (await call(server_url, token, "list_task_history", id="VP-1"))["items"]
    ]
    assert "rework_requested" in history_kinds and "approved" in history_kinds
