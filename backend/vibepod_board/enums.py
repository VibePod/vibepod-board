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


BOARD_COLUMNS: tuple[BoardColumn, ...] = tuple(BoardColumn)
