"""Review basics: the project's review settings, the open reviews of a task, and ending them.
Kept apart from `reviews.py` so the writes that take a card out of Review (moves by hand,
rework, cancels, sign-offs) can end its reviews without importing the whole review flow."""

from datetime import datetime

from sqlmodel import Session, col, select

from vibepod_board.enums import ReviewVerdict, TaskEventKind
from vibepod_board.errors import BadRequest
from vibepod_board.services.history import add_task_event
from vibepod_board.tables import TaskReviewRow

MIN_REQUIRED_APPROVALS = 1
MAX_REQUIRED_APPROVALS = 5
MIN_REVIEW_ROUNDS = 1
MAX_REVIEW_ROUNDS = 10


def validate_review_settings(required_approvals: int | None, max_review_rounds: int | None) -> None:
    for name, value, low, high in (
        ("requiredApprovals", required_approvals, MIN_REQUIRED_APPROVALS, MAX_REQUIRED_APPROVALS),
        ("maxReviewRounds", max_review_rounds, MIN_REVIEW_ROUNDS, MAX_REVIEW_ROUNDS),
    ):
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise BadRequest(f"{name} must be an integer from {low} to {high}")


def short_sha(head_sha: str | None) -> str:
    return head_sha[:12] if head_sha else "an unknown commit"


def open_reviews(session: Session, idea_id: str, lock: bool = False) -> list[TaskReviewRow]:
    query = (
        select(TaskReviewRow)
        .where(TaskReviewRow.idea_id == idea_id, col(TaskReviewRow.ended_at).is_(None))
        .order_by(col(TaskReviewRow.created_at), col(TaskReviewRow.id))
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return list(session.exec(query).all())


def end_review(
    session: Session,
    review: TaskReviewRow,
    timestamp: datetime,
    reason: str,
    actor: str | None,
    verdict: ReviewVerdict | None = None,
) -> None:
    """Ends an open review without a decision on the task: expired, cancelled, released or
    failed. Its holder learns it with its next heartbeat reply, as a cancel instruction."""
    review.ended_at = timestamp
    review.updated_at = timestamp
    review.verdict = verdict
    review.ended_reason = reason
    add_task_event(
        session,
        review.idea_id,
        TaskEventKind.REVIEW_ENDED,
        f"Review by {review.reviewer} of {short_sha(review.head_sha)} ended: {reason}",
        actor,
        timestamp,
    )


def end_open_reviews(
    session: Session,
    idea_id: str,
    timestamp: datetime,
    reason: str,
    actor: str | None,
    keep: str | None = None,
) -> int:
    """Ends every open review of a task but `keep`; their verdicts will be refused."""
    ended = 0
    for review in open_reviews(session, idea_id, lock=True):
        if review.id != keep:
            end_review(session, review, timestamp, reason, actor)
            ended += 1
    session.flush()
    return ended
