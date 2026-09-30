"""Review basics: the project's review settings, the open reviews of a task, ending them, and
the approvals of a card's head commit. Kept apart from `reviews.py` so the writes that take a
card out of Review (moves by hand, rework, cancels, sign-offs, a lowered requirement) can end
its reviews without importing the whole review flow."""

from datetime import datetime

from sqlmodel import Session, col, select

from vibepod_board.enums import BoardColumn, IdeaStatus, ReviewVerdict, TaskEventKind
from vibepod_board.errors import BadRequest
from vibepod_board.services.common import add_activity, lock
from vibepod_board.services.history import add_task_event
from vibepod_board.services.references import task_keys
from vibepod_board.tables import BoardCardRow, IdeaRow, TaskReviewRow

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


def _at_head(card: BoardCardRow) -> list:
    """Conditions matching the reviews of the card's current head commit. Without a known
    commit, the reviews since the last hand-over stand in for it."""
    if card.head_sha:
        return [TaskReviewRow.head_sha == card.head_sha]
    conditions = [col(TaskReviewRow.head_sha).is_(None)]
    if card.handed_over_at is not None:
        conditions.append(col(TaskReviewRow.created_at) >= card.handed_over_at)
    return conditions


def approvers(session: Session, card: BoardCardRow) -> list[str]:
    """Distinct reviewers that approved the card's current head commit, first approval first."""
    if not card.idea_id:
        return []
    rows = session.exec(
        select(TaskReviewRow.reviewer)
        .where(
            TaskReviewRow.idea_id == card.idea_id,
            TaskReviewRow.verdict == ReviewVerdict.APPROVE,
            *_at_head(card),
        )
        .order_by(col(TaskReviewRow.ended_at))
    ).all()
    return list(dict.fromkeys(rows))


def move_to_pr_ready(
    session: Session,
    idea: IdeaRow,
    card: BoardCardRow,
    key: str,
    count: str,
    timestamp: datetime,
    actor: str | None,
) -> None:
    """Moves a card whose head commit has the approvals it needs from Review to PR ready,
    ending the reviews still open on it. `count` reads like "2 of 2"."""
    end_open_reviews(session, idea.id, timestamp, "the task is ready for a PR", actor)
    card.column_name = BoardColumn.PR_READY
    card.review_rounds = 0
    card.blocked_at = None
    card.blocked_reason = None
    card.question = None
    card.updated_at = timestamp
    idea.updated_at = timestamp
    add_activity(
        session, "board.approved", f"{key} is ready for a PR ({count} approvals)", timestamp
    )


def promote_approved(
    session: Session,
    project_id: str,
    required_approvals: int,
    timestamp: datetime,
    actor: str | None,
) -> int:
    """Moves every card in Review whose head commit already has `required_approvals`
    approvals to PR ready, as the approval reaching the count would have: once the
    requirement dropped, no review is left to give the verdict that moves it. Blocked cards
    wait for their human; answering them promotes them."""
    candidates = session.exec(
        select(BoardCardRow.id, BoardCardRow.idea_id)
        .where(
            BoardCardRow.project_id == project_id,
            BoardCardRow.column_name == BoardColumn.REVIEW,
            col(BoardCardRow.idea_id).is_not(None),
        )
        .order_by(col(BoardCardRow.created_at), col(BoardCardRow.id))
    ).all()
    promoted = 0
    for card_id, idea_id in candidates:
        # Task, then card, as every review write locks them.
        idea = lock(session, IdeaRow, idea_id)
        card = lock(session, BoardCardRow, card_id)
        if promote_if_approved(session, idea, card, required_approvals, timestamp, actor):
            promoted += 1
    return promoted


def promote_if_approved(
    session: Session,
    idea: IdeaRow,
    card: BoardCardRow,
    required_approvals: int,
    timestamp: datetime,
    actor: str | None,
) -> bool:
    """Moves an unblocked card in Review to PR ready when its head commit already has the
    approvals required, recording why in the task history. Task and card must be locked."""
    if (
        card.column_name != BoardColumn.REVIEW
        or card.blocked_at is not None
        or card.archived_at is not None
        or idea.status == IdeaStatus.DENIED
    ):
        return False
    approved = approvers(session, card)
    if len(approved) < required_approvals:
        return False
    key = task_keys(session, [idea.id]).get(idea.id, idea.id)
    count = f"{len(approved)} of {required_approvals}"
    add_task_event(
        session,
        idea.id,
        TaskEventKind.APPROVED,
        f"Approved at {short_sha(card.head_sha)} ({count}): the project requires "
        f"{_approvals(required_approvals)} now",
        actor,
        timestamp,
    )
    move_to_pr_ready(session, idea, card, key, count, timestamp, actor)
    return True


def _approvals(count: int) -> str:
    return f"{count} approval" + ("" if count == 1 else "s")
