"""Rebases: rebase workers bringing a task's branch back in line, on request from the board.

A task's branch can diverge from origin's or fall far behind the default branch, and its card
is then blocked until someone fixes the branch. Asking for a rebase from the board, on a card
with a branch in Planned, Review or PR ready, records the request on the card: what to rebase
onto (origin's task branch, the project's default branch, or both), by whom and when. The
request clears the card's block while it is pending, ends the open reviews of the head it is
about to replace, and keeps implementation and review claims off the card until it ends.

A rebase worker claims in rebase mode (`claims.claim_task`) and gets the oldest pending
request of the project. Only one worker holds a request at a time; the hold is a lease like a
claim's, renewed by the worker's heartbeats, and one that lapses or is released leaves the
request pending for the next worker. The card stays in its column throughout.

The worker reports the outcome: `rebased` with the new head commit ends the request and leaves
the card unblocked in its column, so a planned task can be claimed again and a reviewed one is
reviewed at its new head; `needs_input` blocks the card with the worker's question, and the
answer unblocks it in its column with the request still pending; `failed` ends the request and
blocks the card. A move by hand or a cancel from the board ends the request.

Every write locks the task and then its card, the order the other claim calls take.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, assert_can_access_project
from vibepod_board.enums import (
    BoardColumn,
    IdeaStatus,
    RebaseOutcome,
    RebaseTarget,
    TaskEventKind,
)
from vibepod_board.errors import BadRequest, Conflict
from vibepod_board.schemas import BoardCard, ClaimResult, now
from vibepod_board.services.claims import (
    DEFAULT_LEASE_SECONDS,
    _card_of,
    _claim_result,
    _column_label,
    _holder,
    _key,
    _locked_or_none,
    lease_for,
    normalize_sha,
)
from vibepod_board.services.common import (
    add_activity,
    assert_unchanged,
    card_from_row,
    lock,
    transactional,
)
from vibepod_board.services.dependencies import decorate_card
from vibepod_board.services.history import actor_for, add_task_event
from vibepod_board.services.open_rebases import (
    drop_hold,
    end_rebase_request,
    end_request,
    rebase_held,
    rebase_pending,
    rebase_request,
    target_label,
)
from vibepod_board.services.open_reviews import end_open_reviews, short_sha
from vibepod_board.services.references import require_card_ref, require_idea_ref
from vibepod_board.tables import BoardCardRow, IdeaRow, ProjectRow

# Columns whose cards carry a branch worth rebasing: planned work being reworked, work up for
# review and work waiting for its pull request.
REBASE_COLUMNS = (BoardColumn.PLANNED, BoardColumn.REVIEW, BoardColumn.PR_READY)
NOTHING_TO_REBASE = "No rebase request can be claimed"


def _locked(
    session: Session, access: AccessContext, reference: str
) -> tuple[IdeaRow, BoardCardRow]:
    found = require_card_ref(session, access, reference)
    assert_can_access_project(access, found.project_id)
    if found.archived_at is not None:
        raise Conflict("Board card is archived; unarchive it first")
    if not found.idea_id:
        raise Conflict("Board card has no task, so it has no branch to rebase")
    return lock(session, IdeaRow, found.idea_id), lock(session, BoardCardRow, found.id)


def _echo(session: Session, card: BoardCardRow) -> BoardCard:
    session.flush()
    return decorate_card(session, card_from_row(card))


@transactional
def request_rebase(
    session: Session,
    access: AccessContext,
    reference: str,
    target: RebaseTarget | None = None,
    expected_updated_at: str | datetime | None = None,
) -> BoardCard:
    """Asks for a rebase of the card's branch onto `target` (both by default). The card keeps
    its column; its block is cleared while the request is pending, and the open reviews of
    its head end, since the rebase replaces that head. Asking again before a worker took the
    request changes its target; a request a worker holds is refused."""
    target = RebaseTarget(target or RebaseTarget.BOTH)
    idea, card = _locked(session, access, reference)
    assert_unchanged("Board card", expected_updated_at, card.updated_at)
    key = _key(session, idea)
    if card.column_name not in REBASE_COLUMNS:
        raise Conflict(
            f"Task {key} is in {_column_label(card.column_name)}; only tasks in planned, "
            "review or PR ready are rebased"
        )
    if not card.branch_name:
        raise Conflict(f"Task {key} names no branch to rebase")
    if card.claimed_at is not None:
        raise Conflict(f"Task {key} is claimed by {card.assignee}; cancel its run first")
    if card.question:
        raise Conflict(f"Task {key} waits for an answer to its question; answer it first")
    timestamp = now()
    if rebase_held(card, timestamp):
        raise Conflict(f"Task {key} is being rebased by {card.rebase_holder}")
    actor = actor_for(session, access)
    message = f"Rebase of {card.branch_name} onto {target_label(target)} requested"
    if card.blocked_at is not None:
        message += f"; cleared the block: {card.blocked_reason}"
        card.blocked_at = None
        card.blocked_reason = None
    if card.column_name == BoardColumn.REVIEW:
        end_open_reviews(session, idea.id, timestamp, f"{actor} requested a rebase", actor)
    drop_hold(card)
    card.rebase_target = target
    card.rebase_requested_by = actor
    card.rebase_requested_at = timestamp
    card.updated_at = timestamp
    idea.updated_at = timestamp
    add_task_event(session, idea.id, TaskEventKind.REBASE_REQUESTED, message, actor, timestamp)
    add_activity(session, "board.rebase_requested", f"{actor} asked to rebase {key}", timestamp)
    return _echo(session, card)


@transactional
def cancel_rebase(
    session: Session,
    access: AccessContext,
    reference: str,
    reason: str | None = None,
) -> BoardCard:
    """Withdraws a pending request; a worker holding it is told to cancel. The card stays
    where it is, unblocked."""
    idea, card = _locked(session, access, reference)
    key = _key(session, idea)
    if not rebase_pending(card):
        raise Conflict(f"Task {key} has no rebase request to cancel")
    timestamp = now()
    actor = actor_for(session, access)
    reason = (reason or "").strip()
    end_rebase_request(
        session, card, f"cancelled by {actor}" + (f": {reason}" if reason else ""), actor, timestamp
    )
    card.updated_at = timestamp
    idea.updated_at = timestamp
    add_activity(session, "board.cancelled", f"{actor} cancelled the rebase of {key}", timestamp)
    return _echo(session, card)


def _rebase_obstacle(card: BoardCardRow | None, idea: IdeaRow, timestamp: datetime) -> str | None:
    """Why a rebase worker cannot take the task's request, or None when it can."""
    if card is None or card.archived_at is not None:
        return "it is not on the board"
    if not rebase_pending(card):
        return "no rebase was requested"
    if card.column_name not in REBASE_COLUMNS:
        return f"its card is in {_column_label(card.column_name)}"
    if not card.branch_name:
        return "its card names no branch to rebase"
    if idea.status == IdeaStatus.DENIED:
        return "it was denied"
    if card.question:
        return "it waits for an answer to its question"
    if card.claimed_at is not None:
        return f"it is claimed by {card.assignee}"
    if rebase_held(card, timestamp):
        return f"{card.rebase_holder} is rebasing it"
    return None


def _rebase_claimed(session: Session, idea: IdeaRow, card: BoardCardRow) -> ClaimResult:
    result = _claim_result(session, idea, card)
    return result.model_copy(update={"rebase": rebase_request(card)})


def _next_rebase(
    session: Session, project: ProjectRow, exclude: set[str], timestamp: datetime
) -> tuple[IdeaRow, BoardCardRow] | None:
    """The oldest pending request a worker can take, with the task and card locked."""
    pending = session.exec(
        select(BoardCardRow.id, BoardCardRow.idea_id)
        .where(
            BoardCardRow.project_id == project.id,
            col(BoardCardRow.rebase_target).is_not(None),
            col(BoardCardRow.archived_at).is_(None),
        )
        .order_by(col(BoardCardRow.rebase_requested_at), col(BoardCardRow.id))
    ).all()
    for card_id, idea_id in pending:
        if not idea_id or idea_id in exclude:
            continue
        idea = session.get(IdeaRow, idea_id)
        card = session.get(BoardCardRow, card_id)
        if idea is None or _rebase_obstacle(card, idea, timestamp) is not None:
            continue
        # Re-read under lock: the request may have been taken, cancelled or moved since.
        idea = lock(session, IdeaRow, idea_id)
        card = lock(session, BoardCardRow, card_id)
        if _rebase_obstacle(card, idea, timestamp) is None:
            return idea, card
    return None


def claim_rebase(
    session: Session,
    access: AccessContext,
    project: ProjectRow,
    holder: str,
    task: str | None,
    exclude: set[str],
    lease: timedelta,
) -> ClaimResult:
    """The rebase mode of `claims.claim_task`, inside its transaction and project lock, so two
    workers never hold one request. Asking again for a request the worker holds renews it."""
    timestamp = now()
    if task:
        idea = require_idea_ref(session, access, task)
        if idea.project_id != project.id:
            raise BadRequest(f"Task {_key(session, idea)} belongs to another project")
        idea = lock(session, IdeaRow, idea.id)
        card = _card_of(session, idea.id)
        if card is not None:
            card = lock(session, BoardCardRow, card.id)
            if rebase_pending(card) and card.rebase_holder == holder and rebase_held(card):
                card.rebase_expires_at = timestamp + lease
                return _rebase_claimed(session, idea, card)
        obstacle = _rebase_obstacle(card, idea, timestamp)
        if obstacle is not None:
            raise Conflict(f"Task {_key(session, idea)} cannot be rebased: {obstacle}")
        assert card is not None
    else:
        chosen = _next_rebase(session, project, exclude, timestamp)
        if chosen is None:
            return ClaimResult(claimed=False, reason=NOTHING_TO_REBASE)
        idea, card = chosen

    card.rebase_holder = holder
    card.rebase_claimed_at = timestamp
    card.rebase_expires_at = timestamp + lease
    on = f" of {card.branch_name}" if card.branch_name else ""
    add_task_event(
        session,
        idea.id,
        TaskEventKind.REBASE_STARTED,
        f"Rebase{on} by {holder} onto {target_label(card.rebase_target)} started",
        holder,
        timestamp,
    )
    key = _key(session, idea)
    add_activity(
        session, "board.rebase_started", f"{holder} rebases {key}: {idea.title}", timestamp
    )
    return _rebase_claimed(session, idea, card)


def _held_rebase(
    session: Session, access: AccessContext, reference: str, holder: str
) -> tuple[IdeaRow, BoardCardRow]:
    """The task and card of a rebase request the caller holds, both locked."""
    found = require_card_ref(session, access, reference)
    assert_can_access_project(access, found.project_id)
    if not found.idea_id:
        raise Conflict("Board card has no task, so it has no rebase")
    idea = lock(session, IdeaRow, found.idea_id)
    card = lock(session, BoardCardRow, found.id)
    key = _key(session, idea)
    if not rebase_pending(card) or card.rebase_holder != holder:
        if not rebase_pending(card):
            state = "no rebase is pending"
        elif card.rebase_holder and rebase_held(card):
            state = f"{card.rebase_holder} holds it"
        else:
            state = "nobody holds the request"
        raise Conflict(f"Task {key} is not being rebased by {holder}: {state}")
    if not rebase_held(card):
        # Over even before the sweep ends it, as for a lapsed claim (see `claims._held`).
        raise Conflict(f"Task {key} is not being rebased by {holder}: the hold expired")
    return idea, card


@transactional
def renew_rebase(
    session: Session,
    access: AccessContext,
    reference: str,
    assignee: str,
    lease_seconds: int | None = None,
    default_lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> BoardCard:
    """Extends a rebase the caller holds; heartbeats of its worker do the same. `updated_at`
    is left alone, as for any renewal."""
    lease = lease_for(lease_seconds, default_lease_seconds)
    _idea, card = _held_rebase(session, access, reference, _holder(assignee))
    card.rebase_expires_at = now() + lease
    return _echo(session, card)


@transactional
def release_rebase(
    session: Session,
    access: AccessContext,
    reference: str,
    assignee: str,
    note: str | None = None,
) -> BoardCard:
    """Gives a rebase back without doing it, such as on shutdown: the request stays pending
    for the next rebase worker."""
    holder = _holder(assignee)
    idea, card = _held_rebase(session, access, reference, holder)
    timestamp = now()
    apply_rebase_release(session, idea, card, holder, (note or "").strip(), timestamp)
    return _echo(session, card)


def apply_rebase_release(
    session: Session,
    idea: IdeaRow,
    card: BoardCardRow,
    holder: str,
    note: str,
    timestamp: datetime,
) -> None:
    drop_hold(card)
    message = f"Rebase by {holder} released" + (f": {note}" if note else "")
    add_task_event(session, idea.id, TaskEventKind.REBASE_ENDED, message, holder, timestamp)
    add_activity(session, "board.rebase_ended", f"{_key(session, idea)}: {message}", timestamp)


@transactional
def complete_rebase(
    session: Session,
    access: AccessContext,
    reference: str,
    assignee: str,
    outcome: RebaseOutcome,
    head_sha: str | None = None,
    note: str | None = None,
) -> BoardCard:
    """Reports a rebase the caller holds. `rebased` takes the new head commit: the request
    ends and the card stays in its column unblocked, now at that head, so the approvals of
    the old head no longer count. `needs_input` blocks the card with the note as its question;
    the request stays pending and is taken again once someone answers. `failed` ends the
    request and blocks the card with the note."""
    holder = _holder(assignee)
    outcome = RebaseOutcome(outcome)
    sha = normalize_sha(head_sha)
    note = (note or "").strip()
    if outcome == RebaseOutcome.REBASED and not sha:
        raise BadRequest("headSha is required: it is the commit the rebased branch ends at")
    if outcome == RebaseOutcome.NEEDS_INPUT and not note:
        raise BadRequest("A note is required to ask for input: it is the question on the card")
    if outcome == RebaseOutcome.FAILED and not note:
        raise BadRequest("A note is required for a failed rebase: it is the reason on the card")
    idea, card = _held_rebase(session, access, reference, holder)
    key = _key(session, idea)
    timestamp = now()
    suffix = f": {note}" if note else ""
    if outcome == RebaseOutcome.REBASED:
        assert sha is not None
        onto = target_label(card.rebase_target)
        if sha != card.head_sha:
            card.head_sha = sha
            if card.column_name == BoardColumn.REVIEW:
                # A new head for the reviewers: the approvals start over at it.
                card.handed_over_at = timestamp
        end_request(card)
        card.blocked_at = None
        card.blocked_reason = None
        kind = TaskEventKind.REBASED
        message = f"Rebased by {holder} onto {onto} at {short_sha(sha)}{suffix}"
        activity = f"{holder} rebased {key}"
    elif outcome == RebaseOutcome.NEEDS_INPUT:
        drop_hold(card)
        card.blocked_at = timestamp
        card.blocked_reason = f"Needs input: {note}"
        card.question = note
        kind, message = TaskEventKind.QUESTION, note
        activity = f"{key}: {holder} asks {note}"
    else:
        end_request(card)
        card.blocked_at = timestamp
        card.blocked_reason = f"Rebase failed: {note}"
        kind, message = TaskEventKind.BLOCKED, f"Rebase by {holder} failed{suffix}"
        activity = f"{key}: {message}"
    card.updated_at = timestamp
    idea.updated_at = timestamp
    add_task_event(session, idea.id, kind, message, holder, timestamp)
    add_activity(session, f"board.{kind.value}", activity, timestamp)
    return _echo(session, card)


def renew_rebases_of(session: Session, project_id: str, holder: str, expires_at: datetime) -> None:
    """Keeps the rebases a worker holds alive, as its heartbeat does for claims. A lapsed hold
    stays over: the sweep ends it."""
    session.execute(
        update(BoardCardRow)
        .where(
            col(BoardCardRow.project_id) == project_id,
            col(BoardCardRow.rebase_holder) == holder,
            col(BoardCardRow.rebase_target).is_not(None),
            col(BoardCardRow.rebase_expires_at) > now(),
        )
        .values(rebase_expires_at=expires_at)
    )


def held_rebase_cards(session: Session, project_id: str, holder: str) -> Sequence[str]:
    return session.exec(
        select(BoardCardRow.id).where(
            BoardCardRow.project_id == project_id,
            BoardCardRow.rebase_holder == holder,
            col(BoardCardRow.rebase_target).is_not(None),
        )
    ).all()


def release_rebases_of(
    session: Session, project_id: str, holder: str, reason: str, timestamp: datetime
) -> None:
    """Gives back the rebases of a worker that signs off or is removed; the requests stay
    pending."""
    for card_id in held_rebase_cards(session, project_id, holder):
        idea_id = session.exec(select(BoardCardRow.idea_id).where(BoardCardRow.id == card_id)).one()
        if not idea_id:
            continue
        idea = lock(session, IdeaRow, idea_id)
        card = lock(session, BoardCardRow, card_id)
        if rebase_pending(card) and card.rebase_holder == holder:
            apply_rebase_release(session, idea, card, holder, reason, timestamp)


def expire_rebases(session: Session, timestamp: datetime, project_id: str | None) -> int:
    """Drops lapsed rebase holds; the requests stay pending for the next worker."""
    query = select(BoardCardRow.id, BoardCardRow.idea_id).where(
        col(BoardCardRow.rebase_holder).is_not(None),
        col(BoardCardRow.rebase_expires_at) <= timestamp,
    )
    if project_id is not None:
        query = query.where(BoardCardRow.project_id == project_id)
    expired = 0
    for card_id, idea_id in session.exec(query).all():
        if not idea_id or _locked_or_none(session, IdeaRow, idea_id) is None:
            continue
        card = _locked_or_none(session, BoardCardRow, card_id)
        if card is None or card.rebase_holder is None or rebase_held(card, timestamp):
            continue
        holder = card.rebase_holder
        drop_hold(card)
        add_task_event(
            session,
            idea_id,
            TaskEventKind.REBASE_ENDED,
            f"Rebase by {holder} expired without a report; the request is pending again",
            None,
            timestamp,
        )
        expired += 1
    if expired:
        add_activity(session, "board.rebases_expired", f"Expired {expired} rebase holds", timestamp)
    return expired
