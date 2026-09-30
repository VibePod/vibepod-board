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
    PR_READY = "pr_ready"
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
    # The agent asks a question it cannot go on without; the note is the question. The task
    # is blocked until someone answers.
    NEEDS_INPUT = "needs_input"


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
    # A run stopped from the board; the task went back to Planned.
    CANCELLED = "cancelled"
    # The needs-input loop: the agent's question, someone's answer, and a reviewer's feedback
    # when sending a task back from Review.
    QUESTION = "question"
    ANSWER = "answer"
    FEEDBACK = "feedback"


BOARD_COLUMNS: tuple[BoardColumn, ...] = tuple(BoardColumn)


class WorkerStatus(StrEnum):
    """What a worker reports about itself."""

    IDLE = "idle"
    WORKING = "working"
    # Not taking work, such as after reaching a usage limit; the reason says why.
    PAUSED = "paused"


class WorkerState(StrEnum):
    """What the board shows: the reported status, or offline when the heartbeats stopped."""

    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"
    OFFLINE = "offline"


class WorkerStep(StrEnum):
    PREPARING_WORKSPACE = "preparing_workspace"
    AGENT_RUNNING = "agent_running"
    VERIFYING = "verifying"
    HANDING_OVER = "handing_over"


class RunOutcome(StrEnum):
    """How an automated run ended."""

    # The work was handed over to Review.
    DONE = "done"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    # Stopped from the board, or by the worker shutting down.
    CANCELLED = "cancelled"
    # The agent's usage limit was reached; the task went back without counting an attempt.
    USAGE_LIMIT = "usage_limit"
    # The agent asked a question and the task waits for an answer.
    NEEDS_INPUT = "needs_input"


class InstructionType(StrEnum):
    """What a heartbeat reply asks a worker to do."""

    # Automation of the project is paused: take no new task until the instruction is gone.
    PAUSE = "pause"
    # Stop the run in progress, give its task back and sign off.
    STOP = "stop"
    # Stop the run of the named task; it is no longer claimed by the worker.
    CANCEL = "cancel"
