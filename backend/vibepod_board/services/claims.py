"""Claims: automated runners taking planned work off the board.

A runner claims the next planned task of a project in one step. The task's card moves to In
progress and the runner becomes its assignee, the same holder field people use, so everything
that shows or filters holders shows the claim too. A claim is a lease: the holder renews it
while it works, and a claim that lapses is ended by `expire_claims`, which puts the task back
in Planned and counts the attempt.

The holder reports back by handing the task over to Review with its branch, or by releasing
it: to Planned when a run failed (after too many failed attempts the task is blocked instead),
blocked when it cannot proceed, or released without judging it. A blocked card stays in Planned
and claims skip it until someone puts it in Planned again (see `board._apply_manual_move`).

Claims in one project are serialised on the project row, so two runners claiming at the same
time never get the same task: the second waits for the first to commit and then sees its card
in progress.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, assert_can_access_project
from vibepod_board.enums import BoardColumn, IdeaStatus, ReleaseOutcome, TaskEventKind
from vibepod_board.errors import BadRequest, Conflict, NotFound
from vibepod_board.schemas import BoardCard, Claim, ClaimResult, now
from vibepod_board.services.common import (
    add_activity,
    assert_unchanged,
    card_from_row,
    lock,
    normalize_list,
    normalize_optional_text,
    transactional,
)
from vibepod_board.services.dependencies import decorate_card, decorate_idea
from vibepod_board.services.history import actor_for, add_task_event
from vibepod_board.services.ideas import work_order
from vibepod_board.services.references import (
    require_card_ref,
    require_idea_ref,
    resolve_project_id,
    task_keys,
)
from vibepod_board.services.views import View, project_cards, project_ideas
from vibepod_board.tables import BoardCardRow, IdeaRow, ProjectRow

DEFAULT_LEASE_SECONDS = 15 * 60
DEFAULT_MAX_ATTEMPTS = 3
MIN_LEASE_SECONDS = 30
MAX_LEASE_SECONDS = 24 * 60 * 60
NOTHING_TO_CLAIM = "No planned task can be claimed"


def lease_for(lease_seconds: int | None, default_seconds: int = DEFAULT_LEASE_SECONDS) -> timedelta:
    seconds = lease_seconds or default_seconds
    if not MIN_LEASE_SECONDS <= seconds <= MAX_LEASE_SECONDS:
        raise BadRequest(
            f"leaseSeconds must be between {MIN_LEASE_SECONDS} and {MAX_LEASE_SECONDS}"
        )
    return timedelta(seconds=seconds)


def _holder(assignee: str | None) -> str:
    holder = (assignee or "").strip()
    if not holder:
        raise BadRequest("assignee is required: it names who holds the claim")
    return holder


def _key(session: Session, idea: IdeaRow) -> str:
    return task_keys(session, [idea.id]).get(idea.id, idea.id)


def _column_label(column: str) -> str:
    return column.replace("_", " ")


def _count(number: int, singular: str, plural: str | None = None) -> str:
    return f"{number} {singular if number == 1 else plural or singular + 's'}"


def _obstacle(
    card: BoardCardRow | None,
    idea: IdeaRow,
    blocked_by: Sequence[str],
    labels: Sequence[str],
    min_readiness: int | None,
) -> str | None:
    """Why a task cannot be claimed, or None when it can."""
    if card is None or card.archived_at is not None:
        return "it is not on the board"
    if card.column_name != BoardColumn.PLANNED:
        return f"its card is in {_column_label(card.column_name)}, not planned"
    if card.blocked_at is not None:
        return f"it is blocked: {card.blocked_reason}"
    if idea.status == IdeaStatus.DENIED:
        return "it was denied"
    if idea.assignee:
        return f"it is held by {idea.assignee}"
    if blocked_by:
        waiting = _count(len(blocked_by), "unfinished dependency", "unfinished dependencies")
        return f"it waits for {waiting}"
    held = {label.casefold() for label in idea.labels}
    missing = [label for label in labels if label.casefold() not in held]
    if missing:
        return f"it lacks the label {', '.join(missing)}"
    if min_readiness is not None and (idea.readiness_score or 0) < min_readiness:
        shown = idea.readiness_score if idea.readiness_score is not None else "unscored"
        return f"its readiness ({shown}) is below {min_readiness}"
    return None


def _lock_project(session: Session, project_id: str) -> ProjectRow:
    return session.exec(
        select(ProjectRow)
        .where(ProjectRow.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _card_of(session: Session, idea_id: str) -> BoardCardRow | None:
    return session.exec(
        select(BoardCardRow)
        .where(BoardCardRow.idea_id == idea_id)
        .order_by(col(BoardCardRow.created_at), col(BoardCardRow.id))
    ).first()


def _next_claimable(
    session: Session,
    access: AccessContext,
    project_id: str,
    labels: Sequence[str],
    min_readiness: int | None,
    exclude: set[str],
) -> tuple[IdeaRow, BoardCardRow] | None:
    """The first task in the work order that can be claimed, with both rows locked."""
    for item in work_order(session, access, project_id).items:
        if item.column != BoardColumn.PLANNED or item.card_id is None or item.is_blocked:
            continue
        if item.id in exclude:
            continue
        idea = session.get(IdeaRow, item.id)
        card = session.get(BoardCardRow, item.card_id)
        if idea is None or _obstacle(card, idea, [], labels, min_readiness) is not None:
            continue
        # Re-read under lock: a card write does not take the project lock, so the card may
        # have moved since the work order was read.
        idea = lock(session, IdeaRow, item.id)
        card = lock(session, BoardCardRow, item.card_id)
        if _obstacle(card, idea, [], labels, min_readiness) is None:
            return idea, card
    return None


def _excluded_id(session: Session, access: AccessContext, reference: str) -> str:
    """A task to pass over; one that no longer resolves is passed over by name."""
    try:
        return require_idea_ref(session, access, reference).id
    except NotFound:
        return reference.strip()


def _end_claim(card: BoardCardRow, idea: IdeaRow | None, timestamp: datetime) -> None:
    card.claimed_at = None
    card.claim_expires_at = None
    card.assignee = None
    card.updated_at = timestamp
    if idea is not None:
        idea.assignee = None
        idea.updated_at = timestamp


def _block(card: BoardCardRow, reason: str, timestamp: datetime) -> None:
    card.column_name = BoardColumn.PLANNED
    card.blocked_at = timestamp
    card.blocked_reason = reason


def _claim_result(session: Session, idea: IdeaRow, card: BoardCardRow) -> ClaimResult:
    session.flush()
    task = project_ideas(session, [decorate_idea(session, idea)], View.FULL)[0]
    shown = project_cards(session, [decorate_card(session, card_from_row(card))], View.FULL)[0]
    return ClaimResult(claimed=True, item=Claim(task=task, card=shown))


def _paused(project: ProjectRow) -> ClaimResult:
    reason = project.automation_paused_reason or "Automation is paused from the board"
    return ClaimResult(
        claimed=False, paused=True, reason=f"Automation of {project.key} is paused: {reason}"
    )


@transactional
def claim_task(
    session: Session,
    access: AccessContext,
    project: str,
    assignee: str,
    *,
    task: str | None = None,
    labels: Sequence[str] | None = None,
    min_readiness: int | None = None,
    exclude: Sequence[str] | None = None,
    lease_seconds: int | None = None,
    default_lease_seconds: int = DEFAULT_LEASE_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> ClaimResult:
    """Claims the next planned task of a project in the board's work order, or the named
    task. Asking again for a task the caller already holds renews the claim, so a runner
    that restarts under the same name picks its work back up. `exclude` names tasks the next
    claim passes over, such as a run the runner just saw cancelled from the board."""
    holder = _holder(assignee)
    project_id = resolve_project_id(session, project)
    assert_can_access_project(access, project_id)
    lease = lease_for(lease_seconds, default_lease_seconds)
    wanted = normalize_list(labels)
    if min_readiness is not None and not 1 <= min_readiness <= 10:
        raise BadRequest("minReadiness must be an integer from 1 to 10")

    project_row = _lock_project(session, project_id)
    timestamp = now()
    _expire(session, timestamp, max_attempts, project_id)
    paused = project_row.automation_paused_at is not None

    if task:
        idea = require_idea_ref(session, access, task)
        if idea.project_id != project_id:
            raise BadRequest(f"Task {_key(session, idea)} belongs to another project")
        idea = lock(session, IdeaRow, idea.id)
        card = _card_of(session, idea.id)
        if card is not None:
            card = lock(session, BoardCardRow, card.id)
            if card.claimed_at is not None and card.assignee == holder:
                card.claim_expires_at = timestamp + lease
                return _claim_result(session, idea, card)
        if paused:
            return _paused(project_row)
        blocked_by = decorate_idea(session, idea).blocked_by
        obstacle = _obstacle(card, idea, blocked_by, wanted, min_readiness)
        if obstacle is not None:
            raise Conflict(f"Task {_key(session, idea)} cannot be claimed: {obstacle}")
        assert card is not None
    else:
        if paused:
            return _paused(project_row)
        skipped = {_excluded_id(session, access, reference) for reference in exclude or []}
        chosen = _next_claimable(session, access, project_id, wanted, min_readiness, skipped)
        if chosen is None:
            return ClaimResult(claimed=False, reason=NOTHING_TO_CLAIM)
        idea, card = chosen

    attempt = card.attempts + 1
    card.column_name = BoardColumn.IN_PROGRESS
    card.claimed_at = timestamp
    card.claim_expires_at = timestamp + lease
    card.assignee = holder
    card.updated_at = timestamp
    idea.assignee = holder
    idea.updated_at = timestamp
    shown_attempt = f" (attempt {attempt})" if attempt > 1 else ""
    add_task_event(
        session,
        idea.id,
        TaskEventKind.CLAIMED,
        f"Claimed by {holder}{shown_attempt}",
        holder,
        timestamp,
    )
    key = _key(session, idea)
    add_activity(session, "board.claimed", f"{holder} claimed {key}: {idea.title}", timestamp)
    return _claim_result(session, idea, card)


def _held(
    session: Session, access: AccessContext, reference: str, holder: str
) -> tuple[IdeaRow, BoardCardRow]:
    """The task and card of a claim the caller holds, both locked."""
    found = require_card_ref(session, access, reference)
    assert_can_access_project(access, found.project_id)
    if not found.idea_id:
        raise Conflict("Board card has no task, so it cannot be claimed")
    idea = lock(session, IdeaRow, found.idea_id)
    card = lock(session, BoardCardRow, found.id)
    if card.claimed_at is None or card.assignee != holder:
        state = f"{card.assignee} holds it" if card.claimed_at else "it is not claimed"
        raise Conflict(f"Task {_key(session, idea)} is not claimed by {holder}: {state}")
    return idea, card


@transactional
def renew_claim(
    session: Session,
    access: AccessContext,
    reference: str,
    assignee: str,
    lease_seconds: int | None = None,
    default_lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> BoardCard:
    """Extends a claim the caller holds. `updated_at` is left alone: a renewal is not an
    edit, and runners renew often."""
    lease = lease_for(lease_seconds, default_lease_seconds)
    _idea, card = _held(session, access, reference, _holder(assignee))
    card.claim_expires_at = now() + lease
    session.flush()
    return decorate_card(session, card_from_row(card))


@transactional
def hand_over_task(
    session: Session,
    access: AccessContext,
    reference: str,
    assignee: str,
    branch_name: str | None = None,
    note: str | None = None,
    expected_updated_at: str | None = None,
) -> BoardCard:
    """Moves a claimed task to Review with the branch that holds the work, and ends the
    claim. A successful run clears the failed attempts."""
    holder = _holder(assignee)
    idea, card = _held(session, access, reference, holder)
    assert_unchanged("Board card", expected_updated_at, card.updated_at)
    timestamp = now()
    branch = normalize_optional_text(branch_name)
    _end_claim(card, idea, timestamp)
    card.column_name = BoardColumn.REVIEW
    card.attempts = 0
    if branch:
        card.branch_name = branch
    message = "Handed over to review"
    if branch:
        message += f" on branch {branch}"
    if note and note.strip():
        message += f": {note.strip()}"
    add_task_event(session, idea.id, TaskEventKind.HANDED_OVER, message, holder, timestamp)
    add_activity(
        session, "board.handed_over", f"{holder} handed over {_key(session, idea)}", timestamp
    )
    session.flush()
    return decorate_card(session, card_from_row(card))


@transactional
def release_task(
    session: Session,
    access: AccessContext,
    reference: str,
    assignee: str,
    outcome: ReleaseOutcome = ReleaseOutcome.FAILED,
    note: str | None = None,
    max_attempts: int | None = None,
    default_max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> BoardCard:
    """Gives a claimed task back to Planned. A failed run counts an attempt, and the attempt
    that reaches `max_attempts` blocks the task instead; `blocked` blocks it outright, with the
    note as the reason shown on the card; `needs_input` blocks it with the note as a question
    that waits for an answer; `released` counts nothing."""
    card = apply_release(
        session,
        access,
        reference,
        _holder(assignee),
        ReleaseOutcome(outcome),
        note,
        max_attempts or default_max_attempts,
        now(),
    )
    return decorate_card(session, card_from_row(card))


def apply_release(
    session: Session,
    access: AccessContext,
    reference: str,
    holder: str,
    outcome: ReleaseOutcome,
    note: str | None,
    max_attempts: int,
    timestamp: datetime,
) -> BoardCardRow:
    """The release inside the caller's transaction, so signing a worker off can release the
    claims it still holds in the same write."""
    note = (note or "").strip()
    if outcome == ReleaseOutcome.BLOCKED and not note:
        raise BadRequest("A note is required to block a task: it is the reason shown on the card")
    if outcome == ReleaseOutcome.NEEDS_INPUT and not note:
        raise BadRequest("A note is required to ask for input: it is the question on the card")
    if max_attempts < 1:
        raise BadRequest("maxAttempts must be at least 1")
    idea, card = _held(session, access, reference, holder)
    _end_claim(card, idea, timestamp)
    card.column_name = BoardColumn.PLANNED
    suffix = f": {note}" if note else ""
    if outcome == ReleaseOutcome.FAILED:
        card.attempts += 1
        if card.attempts >= max_attempts:
            _block(card, f"Failed {_count(card.attempts, 'attempt')}{suffix}", timestamp)
            kind, message = (
                TaskEventKind.BLOCKED,
                f"Attempt {card.attempts} failed; blocked after "
                f"{_count(card.attempts, 'failed attempt')}{suffix}",
            )
        else:
            kind, message = (
                TaskEventKind.FAILED,
                f"Attempt {card.attempts} of {max_attempts} failed{suffix}",
            )
    elif outcome == ReleaseOutcome.BLOCKED:
        _block(card, note, timestamp)
        kind, message = TaskEventKind.BLOCKED, f"Blocked: {note}"
    elif outcome == ReleaseOutcome.NEEDS_INPUT:
        _block(card, f"Needs input: {note}", timestamp)
        card.question = note
        kind, message = TaskEventKind.QUESTION, note
    else:
        kind, message = TaskEventKind.RELEASED, f"Released{suffix}"
    add_task_event(session, idea.id, kind, message, holder, timestamp)
    add_activity(session, f"board.{kind.value}", f"{_key(session, idea)}: {message}", timestamp)
    session.flush()
    return card


@transactional
def cancel_run(
    session: Session,
    access: AccessContext,
    reference: str,
    reason: str | None = None,
    expected_updated_at: Any = None,
) -> BoardCard:
    """Stops a running task from the board: the claim ends and the task returns to Planned
    without counting an attempt. The worker learns it with its next heartbeat reply.
    `expected_updated_at` refuses the cancel when the card moved on, such as to a newer run
    than the one the caller saw."""
    found = require_card_ref(session, access, reference)
    assert_can_access_project(access, found.project_id)
    if not found.idea_id:
        raise Conflict("Board card has no task, so it has no run to cancel")
    idea = lock(session, IdeaRow, found.idea_id)
    card = lock(session, BoardCardRow, found.id)
    assert_unchanged("Board card", expected_updated_at, card.updated_at)
    if card.claimed_at is None:
        raise Conflict(f"Task {_key(session, idea)} has no run to cancel: it is not claimed")
    holder = card.assignee
    timestamp = now()
    _end_claim(card, idea, timestamp)
    card.column_name = BoardColumn.PLANNED
    reason = (reason or "").strip()
    message = f"Run by {holder} cancelled" + (f": {reason}" if reason else "")
    actor = actor_for(session, access)
    add_task_event(session, idea.id, TaskEventKind.CANCELLED, message, actor, timestamp)
    add_activity(
        session, "board.cancelled", f"{actor} cancelled the run of {_key(session, idea)}", timestamp
    )
    session.flush()
    return decorate_card(session, card_from_row(card))


def _locked_or_none[T: IdeaRow | BoardCardRow](
    session: Session, table: type[T], row_id: str
) -> T | None:
    """Locks a row unless another transaction holds it; the sweep then leaves it for later
    instead of waiting, so it can never deadlock with a write."""
    return session.exec(
        select(table)
        .where(table.id == row_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).first()


def _expire(
    session: Session, timestamp: datetime, max_attempts: int, project_id: str | None = None
) -> int:
    query = select(BoardCardRow.id, BoardCardRow.idea_id).where(
        col(BoardCardRow.claimed_at).is_not(None),
        col(BoardCardRow.claim_expires_at) <= timestamp,
    )
    if project_id is not None:
        query = query.where(BoardCardRow.project_id == project_id)
    expired = 0
    for card_id, idea_id in session.exec(query).all():
        if not idea_id:
            continue
        idea = _locked_or_none(session, IdeaRow, idea_id)
        card = _locked_or_none(session, BoardCardRow, card_id)
        if idea is None or card is None or card.claimed_at is None:
            continue
        if card.claim_expires_at is None or card.claim_expires_at > timestamp:
            continue
        holder = card.assignee
        _end_claim(card, idea, timestamp)
        card.column_name = BoardColumn.PLANNED
        card.attempts += 1
        if card.attempts >= max_attempts:
            reason = f"Failed {_count(card.attempts, 'attempt')}: the claim by {holder} expired"
            _block(card, reason, timestamp)
            kind = TaskEventKind.BLOCKED
            message = (
                f"Claim by {holder} expired without a report; blocked after "
                f"{_count(card.attempts, 'failed attempt')}"
            )
        else:
            kind = TaskEventKind.EXPIRED
            message = (
                f"Claim by {holder} expired without a report "
                f"(attempt {card.attempts} of {max_attempts})"
            )
        add_task_event(session, idea.id, kind, message, None, timestamp)
        expired += 1
    if expired:
        add_activity(session, "board.claims_expired", f"Expired {expired} task claims", timestamp)
    session.flush()
    return expired


@transactional
def expire_claims(
    session: Session,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    timestamp: datetime | None = None,
) -> int:
    """Ends every lapsed claim: the task returns to Planned and the attempt counts. Runs
    periodically in the app, and before every claim for the project being claimed from."""
    return _expire(session, timestamp or now(), max_attempts)
