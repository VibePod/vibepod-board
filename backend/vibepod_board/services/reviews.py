"""Reviews: review workers deciding whether the work on a task's branch needs rework or is
ready for a PR.

A review worker claims in review mode (`claims.claim_task`) and gets a task from Review, in the
board's work order, with the head commit to review. Its claim is a row of its own, so several
reviewers can hold one task at once while its card stays in Review; the card's holder fields
are left to implementation claims. A review is leased like a claim: heartbeats renew it, and a
review that lapses, is cancelled from the board, or whose worker signs off ends without a
verdict and without counting an implementation attempt.

Reviews and approvals are bound to the card's `head_sha`, the commit the last hand-over put up.
A task moves to PR ready once distinct reviewers approved that commit as often as the project
requires. Any rework verdict sends it back to Planned with the feedback and ends the other open
reviews; after `max_review_rounds` rework verdicts in a row it is blocked in Review for a human
instead. A question for a human blocks it as well and ends the other open reviews; a blocked
task takes no verdict until someone acts on it. A hand-over on a new commit starts the approval
count over, and a verdict for another commit is refused.

Review claims are serialised on the project row like implementation claims, and every write to
a review locks its task, its card and then the review, so counting approvals and open reviews
never races a verdict.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, assert_can_access_project
from vibepod_board.enums import BoardColumn, IdeaStatus, ReviewVerdict, TaskEventKind
from vibepod_board.errors import BadRequest, Conflict, NotFound
from vibepod_board.schemas import ClaimResult, ReviewState, TaskReview, now
from vibepod_board.services.claims import (
    DEFAULT_LEASE_SECONDS,
    _card_of,
    _claim_result,
    _count,
    _holder,
    _key,
    lease_for,
    normalize_sha,
)
from vibepod_board.services.common import (
    add_activity,
    lock,
    new_id,
    require_project,
    transactional,
)
from vibepod_board.services.conversation import apply_rework
from vibepod_board.services.history import actor_for, add_task_event
from vibepod_board.services.ideas import work_order
from vibepod_board.services.open_reviews import (
    _at_head,
    approvers,
    end_open_reviews,
    end_review,
    move_to_pr_ready,
    open_reviews,
    short_sha,
)
from vibepod_board.services.references import require_card_ref, require_idea_ref, task_keys
from vibepod_board.tables import BoardCardRow, IdeaRow, ProjectRow, TaskReviewRow, WorkerRow

NOTHING_TO_REVIEW = "No task in review can be claimed for a review"
# Verdicts that settle a reviewer's view of a head commit: it is not handed that commit again.
# A released review or a question leaves the reviewer free to look again.
SETTLED = (ReviewVerdict.APPROVE, ReviewVerdict.REWORK, ReviewVerdict.FAILED)


def _settled_by(session: Session, card: BoardCardRow, reviewer: str) -> bool:
    return (
        session.exec(
            select(TaskReviewRow.id)
            .where(
                TaskReviewRow.idea_id == card.idea_id,
                TaskReviewRow.reviewer == reviewer,
                col(TaskReviewRow.verdict).in_(list(SETTLED)),
                *_at_head(card),
            )
            .limit(1)
        ).first()
        is not None
    )


def _review_obstacle(
    session: Session,
    project: ProjectRow,
    card: BoardCardRow | None,
    idea: IdeaRow,
    reviewer: str,
    labels: Sequence[str],
    min_readiness: int | None,
) -> str | None:
    """Why the reviewer cannot take a review of the task, or None when it can."""
    if card is None or card.archived_at is not None:
        return "it is not on the board"
    if card.column_name != BoardColumn.REVIEW:
        return f"its card is in {card.column_name.replace('_', ' ')}, not review"
    if card.blocked_at is not None:
        return f"it is blocked: {card.blocked_reason}"
    if idea.status == IdeaStatus.DENIED:
        return "it was denied"
    if not card.branch_name:
        return "its card names no branch to review"
    held = {label.casefold() for label in idea.labels}
    missing = [label for label in labels if label.casefold() not in held]
    if missing:
        return f"it lacks the label {', '.join(missing)}"
    if min_readiness is not None and (idea.readiness_score or 0) < min_readiness:
        shown = idea.readiness_score if idea.readiness_score is not None else "unscored"
        return f"its readiness ({shown}) is below {min_readiness}"
    reviews = open_reviews(session, idea.id)
    if any(review.reviewer == reviewer for review in reviews):
        return f"{reviewer} is already reviewing it"
    if _settled_by(session, card, reviewer):
        return f"{reviewer} already reviewed {short_sha(card.head_sha)}"
    approved = len(approvers(session, card))
    if approved + len(reviews) >= project.required_approvals:
        return (
            f"it has {_count(approved, 'approval')} and {_count(len(reviews), 'open review')} "
            f"of the {project.required_approvals} it needs"
        )
    return None


def _open_review_of(session: Session, idea_id: str, reviewer: str) -> TaskReviewRow | None:
    return session.exec(
        select(TaskReviewRow)
        .where(
            TaskReviewRow.idea_id == idea_id,
            TaskReviewRow.reviewer == reviewer,
            col(TaskReviewRow.ended_at).is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def review_from_row(row: TaskReviewRow, key: str | None = None) -> TaskReview:
    return TaskReview(
        id=row.id,
        idea_id=row.idea_id,
        task_key=key,
        project_id=row.project_id,
        reviewer=row.reviewer,
        worker_id=row.worker_id,
        head_sha=row.head_sha,
        lease_expires_at=row.lease_expires_at,
        open=row.ended_at is None,
        verdict=ReviewVerdict(row.verdict) if row.verdict else None,
        feedback=row.feedback,
        ended_reason=row.ended_reason,
        ended_at=row.ended_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _review_claimed(
    session: Session, idea: IdeaRow, card: BoardCardRow, review: TaskReviewRow
) -> ClaimResult:
    result = _claim_result(session, idea, card)
    return result.model_copy(update={"review": review_from_row(review, _key(session, idea))})


def _next_reviewable(
    session: Session,
    access: AccessContext,
    project: ProjectRow,
    reviewer: str,
    labels: Sequence[str],
    min_readiness: int | None,
    exclude: set[str],
) -> tuple[IdeaRow, BoardCardRow] | None:
    """The first task in Review, in the work order, the reviewer can take; both rows locked."""
    for item in work_order(session, access, project.id).items:
        if item.column != BoardColumn.REVIEW or item.card_id is None or item.id in exclude:
            continue
        idea = session.get(IdeaRow, item.id)
        card = session.get(BoardCardRow, item.card_id)
        if idea is None:
            continue
        if _review_obstacle(session, project, card, idea, reviewer, labels, min_readiness):
            continue
        # Re-read under lock: a verdict or a move may have landed since the work order was read.
        idea = lock(session, IdeaRow, item.id)
        card = lock(session, BoardCardRow, item.card_id)
        if not _review_obstacle(session, project, card, idea, reviewer, labels, min_readiness):
            return idea, card
    return None


def claim_review(
    session: Session,
    access: AccessContext,
    project: ProjectRow,
    reviewer: str,
    task: str | None,
    labels: Sequence[str],
    min_readiness: int | None,
    exclude: set[str],
    lease: timedelta,
) -> ClaimResult:
    """The review mode of `claims.claim_task`, inside its transaction and project lock. Asking
    again for a task the reviewer is reviewing renews that review."""
    timestamp = now()
    if task:
        idea = require_idea_ref(session, access, task)
        if idea.project_id != project.id:
            raise BadRequest(f"Task {_key(session, idea)} belongs to another project")
        idea = lock(session, IdeaRow, idea.id)
        card = _card_of(session, idea.id)
        if card is not None:
            card = lock(session, BoardCardRow, card.id)
            held = _open_review_of(session, idea.id, reviewer)
            if held is not None:
                held.lease_expires_at = timestamp + lease
                return _review_claimed(session, idea, card, held)
        obstacle = _review_obstacle(session, project, card, idea, reviewer, labels, min_readiness)
        if obstacle is not None:
            raise Conflict(f"Task {_key(session, idea)} cannot be reviewed: {obstacle}")
        assert card is not None
    else:
        chosen = _next_reviewable(
            session, access, project, reviewer, labels, min_readiness, exclude
        )
        if chosen is None:
            return ClaimResult(claimed=False, reason=NOTHING_TO_REVIEW)
        idea, card = chosen

    worker_id = session.exec(
        select(WorkerRow.id).where(
            WorkerRow.project_id == project.id,
            WorkerRow.name == reviewer,
            col(WorkerRow.stopped_at).is_(None),
        )
    ).first()
    review = TaskReviewRow(
        id=new_id(),
        idea_id=idea.id,
        project_id=project.id,
        reviewer=reviewer,
        worker_id=worker_id,
        head_sha=card.head_sha,
        lease_expires_at=timestamp + lease,
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(review)
    add_task_event(
        session,
        idea.id,
        TaskEventKind.REVIEW_STARTED,
        f"Review by {reviewer} of {short_sha(card.head_sha)} started",
        reviewer,
        timestamp,
    )
    key = _key(session, idea)
    add_activity(
        session, "board.review_started", f"{reviewer} reviews {key}: {idea.title}", timestamp
    )
    return _review_claimed(session, idea, card, review)


def _held_review(
    session: Session, access: AccessContext, reference: str, reviewer: str
) -> tuple[IdeaRow, BoardCardRow, TaskReviewRow]:
    """The task, card and open review the reviewer holds, all locked."""
    found = require_card_ref(session, access, reference)
    assert_can_access_project(access, found.project_id)
    if not found.idea_id:
        raise Conflict("Board card has no task, so it cannot be reviewed")
    idea = lock(session, IdeaRow, found.idea_id)
    card = lock(session, BoardCardRow, found.id)
    review = _open_review_of(session, idea.id, reviewer)
    if review is None:
        raise Conflict(f"Task {_key(session, idea)} is not being reviewed by {reviewer}")
    if review.lease_expires_at <= now():
        # Over even before the sweep ends it, as for a lapsed claim (see `claims._held`).
        raise Conflict(
            f"Task {_key(session, idea)} is not being reviewed by {reviewer}: the review expired"
        )
    return idea, card, review


@transactional
def renew_review(
    session: Session,
    access: AccessContext,
    reference: str,
    reviewer: str,
    lease_seconds: int | None = None,
    default_lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> TaskReview:
    """Extends a review the caller holds; heartbeats of its worker do the same."""
    lease = lease_for(lease_seconds, default_lease_seconds)
    idea, _card, review = _held_review(session, access, reference, _holder(reviewer))
    review.lease_expires_at = now() + lease
    session.flush()
    return review_from_row(review, _key(session, idea))


@transactional
def submit_review(
    session: Session,
    access: AccessContext,
    reference: str,
    reviewer: str,
    verdict: ReviewVerdict,
    head_sha: str | None = None,
    note: str | None = None,
) -> ReviewState:
    """Ends a review the caller holds with its verdict. `approve` counts an approval of the
    head commit, and the approval that reaches the project's requirement moves the task to PR
    ready. `rework` sends it back to Planned with the note as feedback and ends the other open
    reviews, or blocks it in Review once the reviewers sent it back `max_review_rounds` times in
    a row. `needs_input` blocks it in Review with the note as a question for a human; `failed`
    and `released` end the review without judging the task. A verdict naming another commit
    than the one under review is refused: the branch moved on."""
    holder = _holder(reviewer)
    verdict = ReviewVerdict(verdict)
    sha = normalize_sha(head_sha)
    note = (note or "").strip()
    if verdict == ReviewVerdict.REWORK and not note:
        raise BadRequest("Feedback is required to request rework: it is what the next run gets")
    if verdict == ReviewVerdict.NEEDS_INPUT and not note:
        raise BadRequest("A note is required to ask for input: it is the question on the card")
    idea, card, review = _held_review(session, access, reference, holder)
    key = _key(session, idea)
    judging = verdict in (ReviewVerdict.APPROVE, ReviewVerdict.REWORK)
    if judging and review.head_sha and not sha:
        raise BadRequest(f"headSha is required: the review is of {review.head_sha}")
    if sha and sha != review.head_sha:
        raise Conflict(
            f"Task {key} is reviewed at {short_sha(review.head_sha)}, not {sha[:12]}: "
            "the branch moved on"
        )
    if review.head_sha != card.head_sha or card.column_name != BoardColumn.REVIEW:
        raise Conflict(f"Task {key} was handed over again since the review started")
    ending = verdict in (ReviewVerdict.FAILED, ReviewVerdict.RELEASED)
    if not ending and card.blocked_at is not None:
        # A human decides first; a failed or released review may still end.
        raise Conflict(f"Task {key} is blocked until a human acts: {card.blocked_reason}")
    project = require_project(session, idea.project_id)
    timestamp = now()
    commit = short_sha(review.head_sha)

    if ending:
        reason = ("failed" if verdict == ReviewVerdict.FAILED else "released") + (
            f": {note}" if note else ""
        )
        end_review(session, review, timestamp, reason, holder, verdict)
        review.feedback = note or None
        add_activity(
            session, "board.review_ended", f"{holder} ended the review of {key}", timestamp
        )
        return _state(session, idea, card, project)

    review.verdict = verdict
    review.feedback = note or None
    review.ended_at = timestamp
    review.updated_at = timestamp

    if verdict == ReviewVerdict.APPROVE:
        session.flush()
        approved = approvers(session, card)
        count = f"{len(approved)} of {project.required_approvals}"
        message = f"Approved by {holder} at {commit} ({count})" + (f": {note}" if note else "")
        add_task_event(session, idea.id, TaskEventKind.APPROVED, message, holder, timestamp)
        if len(approved) >= project.required_approvals:
            move_to_pr_ready(session, idea, card, key, count, timestamp, holder)
        else:
            add_activity(session, "board.approved", f"{holder} approved {key} ({count})", timestamp)
    elif verdict == ReviewVerdict.REWORK:
        end_open_reviews(
            session, idea.id, timestamp, f"{holder} requested rework", holder, keep=review.id
        )
        card.review_rounds += 1
        add_task_event(
            session,
            idea.id,
            TaskEventKind.REWORK_REQUESTED,
            f"Rework requested by {holder} at {commit} "
            f"(round {card.review_rounds} of {project.max_review_rounds})",
            holder,
            timestamp,
        )
        if card.review_rounds >= project.max_review_rounds:
            # The loop guard: reviewers and runs disagree, so a human decides.
            add_task_event(session, idea.id, TaskEventKind.FEEDBACK, note, holder, timestamp)
            rounds = _count(card.review_rounds, "time")
            card.blocked_at = timestamp
            card.blocked_reason = f"Sent back for rework {rounds} in a row: {note}"
            card.updated_at = timestamp
            idea.updated_at = timestamp
            add_task_event(
                session,
                idea.id,
                TaskEventKind.BLOCKED,
                f"Blocked in review after {_count(card.review_rounds, 'rework round')} in a row",
                holder,
                timestamp,
            )
        else:
            apply_rework(session, idea, card, note, holder, timestamp)
        add_activity(session, "task.rework", f"{holder} sent {key} back for rework", timestamp)
    else:
        # The task waits for an answer: the other reviews end, as for rework, and their
        # reviewers may look again once it is answered.
        end_open_reviews(
            session, idea.id, timestamp, f"{holder} asked for input", holder, keep=review.id
        )
        card.blocked_at = timestamp
        card.blocked_reason = f"Needs input: {note}"
        card.question = note
        card.updated_at = timestamp
        idea.updated_at = timestamp
        add_task_event(session, idea.id, TaskEventKind.QUESTION, note, holder, timestamp)
        add_activity(session, "board.question", f"{key}: {holder} asks {note}", timestamp)
    session.flush()
    return _state(session, idea, card, project)


def _state(
    session: Session, idea: IdeaRow, card: BoardCardRow | None, project: ProjectRow
) -> ReviewState:
    session.flush()
    key = task_keys(session, [idea.id]).get(idea.id)
    rows = session.exec(
        select(TaskReviewRow)
        .where(TaskReviewRow.idea_id == idea.id)
        .order_by(col(TaskReviewRow.created_at).desc(), col(TaskReviewRow.id).desc())
    ).all()
    approved = approvers(session, card) if card is not None else []
    return ReviewState(
        task_id=idea.id,
        task_key=key,
        column=BoardColumn(card.column_name) if card is not None else None,
        head_sha=card.head_sha if card is not None else None,
        required_approvals=project.required_approvals,
        approvals=len(approved),
        approved_by=approved,
        open_reviews=sum(1 for row in rows if row.ended_at is None),
        review_rounds=card.review_rounds if card is not None else 0,
        max_review_rounds=project.max_review_rounds,
        items=[review_from_row(row, key) for row in rows],
    )


def review_state(session: Session, access: AccessContext, reference: str) -> ReviewState:
    """The reviews of a task and its approvals for the current head commit. `reference` is
    a task reference or, as for the other review endpoints, a board card id."""
    try:
        idea = require_idea_ref(session, access, reference)
    except NotFound:
        try:
            found = require_card_ref(session, access, reference)
        except NotFound:
            raise NotFound(f"Task not found: {reference.strip()}") from None
        if not found.idea_id:
            raise NotFound("Board card has no task, so it has no reviews") from None
        idea = require_idea_ref(session, access, found.idea_id)
    assert_can_access_project(access, idea.project_id)
    project = require_project(session, idea.project_id)
    return _state(session, idea, _card_of(session, idea.id), project)


@transactional
def cancel_review(
    session: Session,
    access: AccessContext,
    reference: str,
    review_id: str,
    reason: str | None = None,
) -> ReviewState:
    """Cancels one open review from the board: it ends without a verdict, the card stays in
    Review, and its worker is told to cancel with its next heartbeat reply."""
    found = require_card_ref(session, access, reference)
    assert_can_access_project(access, found.project_id)
    if not found.idea_id:
        raise Conflict("Board card has no task, so it has no review to cancel")
    idea = lock(session, IdeaRow, found.idea_id)
    card = lock(session, BoardCardRow, found.id)
    review = session.exec(
        select(TaskReviewRow)
        .where(TaskReviewRow.id == review_id.strip(), TaskReviewRow.idea_id == idea.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    key = _key(session, idea)
    if review is None:
        raise NotFound(f"Review not found on task {key}: {review_id}")
    if review.ended_at is not None:
        raise Conflict(f"The review by {review.reviewer} of {key} already ended")
    timestamp = now()
    actor = actor_for(session, access)
    reason = (reason or "").strip()
    end_review(session, review, timestamp, "cancelled" + (f": {reason}" if reason else ""), actor)
    add_activity(
        session,
        "board.cancelled",
        f"{actor} cancelled the review by {review.reviewer} of {key}",
        timestamp,
    )
    return _state(session, idea, card, require_project(session, idea.project_id))


def renew_reviews_of(
    session: Session, project_id: str, reviewer: str, expires_at: datetime, worker_id: str
) -> None:
    """Keeps the open reviews a worker holds alive, as its heartbeat does for claims. A lapsed
    review stays over: the sweep ends it."""
    session.execute(
        update(TaskReviewRow)
        .where(
            col(TaskReviewRow.project_id) == project_id,
            col(TaskReviewRow.reviewer) == reviewer,
            col(TaskReviewRow.ended_at).is_(None),
            col(TaskReviewRow.lease_expires_at) > now(),
        )
        .values(lease_expires_at=expires_at, worker_id=worker_id)
    )
