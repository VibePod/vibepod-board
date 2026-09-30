from enum import StrEnum


class IdeaStatus(StrEnum):
    IDEA = "idea"
    REFINING = "refining"
    READY = "ready"
    DENIED = "denied"


class BoardColumn(StrEnum):
    READY = "ready"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"


class DocumentKind(StrEnum):
    EXECUTION_PLAN = "execution_plan"
    DESIGN = "design"
    NOTES = "notes"


class ReleaseOutcome(StrEnum):
    """How a runner gives a claimed task back without handing it over."""

    # The run failed: the attempt counts, and too many failures block the task.
    FAILED = "failed"
    # The task cannot proceed as it stands; it stays blocked until moved to Planned again.
    BLOCKED = "blocked"
    # The runner stops without judging the task, e.g. on shutdown; no attempt is counted.
    RELEASED = "released"


class TaskEventKind(StrEnum):
    CLAIMED = "claimed"
    HANDED_OVER = "handed_over"
    FAILED = "failed"
    BLOCKED = "blocked"
    RELEASED = "released"
    EXPIRED = "expired"
    # A claim ended because someone moved the card or changed its holder.
    CLAIM_ENDED = "claim_ended"
    UNBLOCKED = "unblocked"


BOARD_COLUMNS: tuple[BoardColumn, ...] = tuple(BoardColumn)
