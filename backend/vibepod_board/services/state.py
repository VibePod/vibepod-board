from sqlmodel import Session

from vibepod_board.access import AccessContext
from vibepod_board.schemas import BoardState
from vibepod_board.services.activity import list_activity
from vibepod_board.services.board import list_cards
from vibepod_board.services.documents import list_documents
from vibepod_board.services.ideas import list_ideas
from vibepod_board.services.projects import list_projects
from vibepod_board.services.transfer import readiness_events_for


def board_state(session: Session, access: AccessContext) -> BoardState:
    """Everything the caller may see, as one document (the MCP `board-state` resource)."""
    ideas = list_ideas(session, access)
    return BoardState(
        projects=list_projects(session, access),
        ideas=ideas,
        board_cards=list_cards(session, access),
        readiness_events=readiness_events_for(session, [idea.id for idea in ideas]),
        documents=list_documents(session, access),
        activity=list_activity(session, access),
    )
