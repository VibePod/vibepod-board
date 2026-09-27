from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, AdminAccess
from vibepod_board.schemas import ActivityEvent
from vibepod_board.services.common import activity_from_row
from vibepod_board.tables import ActivityEventRow


def list_activity(session: Session, access: AccessContext) -> list[ActivityEvent]:
    """The activity log spans all projects, so only the admin sees it."""
    if not isinstance(access, AdminAccess):
        return []
    rows = session.exec(
        select(ActivityEventRow).order_by(col(ActivityEventRow.created_at).desc())
    ).all()
    return [activity_from_row(row) for row in rows]
