"""Pending rebase requests on a card: the state the rest of the board checks and ends, kept
apart from the rebase flow in `rebases.py`, which builds on the claims."""

from datetime import datetime

from sqlmodel import Session

from vibepod_board.enums import BoardColumn, RebaseTarget, TaskEventKind
from vibepod_board.schemas import RebaseRequest, now
from vibepod_board.services.history import add_task_event
from vibepod_board.tables import BoardCardRow

TARGET_LABELS = {
    RebaseTarget.ORIGIN: "origin's task branch",
    RebaseTarget.DEFAULT_BRANCH: "the default branch",
    RebaseTarget.BOTH: "origin's task branch and the default branch",
}


def target_label(target: str | None) -> str:
    return TARGET_LABELS.get(RebaseTarget(target or RebaseTarget.BOTH), str(target))


def rebase_pending(card: BoardCardRow) -> bool:
    return card.rebase_target is not None


def rebase_held(card: BoardCardRow, at: datetime | None = None) -> bool:
    """A worker holds the request until its lease runs out, even before the sweep ends it."""
    return (
        card.rebase_holder is not None
        and card.rebase_expires_at is not None
        and card.rebase_expires_at > (at or now())
    )


def rebase_request(card: BoardCardRow) -> RebaseRequest:
    assert card.rebase_target is not None and card.rebase_requested_at is not None
    return RebaseRequest(
        target=RebaseTarget(card.rebase_target),
        branch_name=card.branch_name,
        head_sha=card.head_sha,
        column=BoardColumn(card.column_name),
        requested_by=card.rebase_requested_by,
        requested_at=card.rebase_requested_at,
        holder=card.rebase_holder,
        claimed_at=card.rebase_claimed_at,
        lease_expires_at=card.rebase_expires_at,
    )


def drop_hold(card: BoardCardRow) -> None:
    card.rebase_holder = None
    card.rebase_claimed_at = None
    card.rebase_expires_at = None


def end_request(card: BoardCardRow) -> None:
    drop_hold(card)
    card.rebase_target = None
    card.rebase_requested_by = None
    card.rebase_requested_at = None


def end_rebase_request(
    session: Session, card: BoardCardRow, reason: str, actor: str | None, timestamp: datetime
) -> None:
    """Ends a pending request of a locked card without a rebase, such as when it is moved by
    hand; a worker holding it is told to cancel with its next heartbeat reply."""
    if not rebase_pending(card) or not card.idea_id:
        return
    holder = card.rebase_holder if rebase_held(card, timestamp) else None
    end_request(card)
    who = f" by {holder}" if holder else ""
    add_task_event(
        session,
        card.idea_id,
        TaskEventKind.REBASE_ENDED,
        f"Rebase{who} ended: {reason}",
        actor,
        timestamp,
    )


def answer_rebase_question(card: BoardCardRow, timestamp: datetime) -> bool:
    """Answering a rebase worker's question unblocks the card in its column, with the request
    pending again for the next rebase worker. False when the card has no rebase pending."""
    if not rebase_pending(card):
        return False
    card.blocked_at = None
    card.blocked_reason = None
    card.question = None
    card.updated_at = timestamp
    return True
