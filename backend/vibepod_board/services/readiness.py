"""LLM-evaluated readiness scores. The idea is the source of truth: a score is mirrored onto
its board card and appended to the history. Neither write bumps `updated_at`, so a later
content edit marks the score stale."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, assert_can_access_project
from vibepod_board.errors import Conflict
from vibepod_board.schemas import BoardCard, Idea, ReadinessEvent, now
from vibepod_board.services.board import ARCHIVED_MESSAGE
from vibepod_board.services.common import (
    add_activity,
    card_from_row,
    new_id,
    normalize_readiness,
    readiness_from_row,
    transactional,
)
from vibepod_board.services.dependencies import decorate_card, decorate_idea
from vibepod_board.services.listing import list_scope
from vibepod_board.services.references import require_card_ref, require_idea_ref
from vibepod_board.tables import BoardCardRow, IdeaReadinessEventRow, IdeaRow


def apply_idea_readiness(
    session: Session, idea: IdeaRow, score: Any, reason: str | None, timestamp: datetime
) -> None:
    """The readiness write, inside the caller's transaction so it can ride along with a
    larger write. Leaves `updated_at` alone on both tables: the UI marks a score stale by
    comparing content time against this one."""
    score, reason = normalize_readiness(score, reason)
    idea.readiness_score = score
    idea.readiness_reason = reason
    idea.readiness_evaluated_at = timestamp
    session.execute(
        update(BoardCardRow)
        .where(col(BoardCardRow.idea_id) == idea.id)
        .values(readiness_score=score, readiness_reason=reason, readiness_evaluated_at=timestamp)
    )
    session.add(
        IdeaReadinessEventRow(
            id=new_id(), idea_id=idea.id, score=score, reason=reason, created_at=timestamp
        )
    )
    session.flush()


@transactional
def set_idea_readiness(
    session: Session, access: AccessContext, idea_id: str, score: Any, reason: str
) -> Idea:
    score, reason = normalize_readiness(score, reason)
    idea = require_idea_ref(session, access, idea_id)
    assert_can_access_project(access, idea.project_id)
    timestamp = now()
    apply_idea_readiness(session, idea, score, reason, timestamp)
    decorated = decorate_idea(session, idea)
    add_activity(session, "idea.readiness", f"Set readiness {score}/10: {idea.title}", timestamp)
    return decorated


def set_card_readiness(
    session: Session, access: AccessContext, card_id: str, score: Any, reason: str
) -> BoardCard:
    card = require_card_ref(session, access, card_id)
    assert_can_access_project(access, card.project_id)

    if card.idea_id:
        set_idea_readiness(session, access, card.idea_id, score, reason)
        session.refresh(card)
        return decorate_card(session, card_from_row(card))

    # A linked card scores its task, which archived cards keep mirroring; a card without a
    # task has nothing else to write to, so it stays frozen while archived.
    if card.archived_at is not None:
        raise Conflict(ARCHIVED_MESSAGE)
    score, reason = normalize_readiness(score, reason)
    card.readiness_score = score
    card.readiness_reason = reason
    card.readiness_evaluated_at = now()
    session.commit()
    return decorate_card(session, card_from_row(card))


def list_idea_readiness(
    session: Session, access: AccessContext, idea_id: str
) -> list[ReadinessEvent]:
    idea = require_idea_ref(session, access, idea_id)
    assert_can_access_project(access, idea.project_id)
    rows = session.exec(
        select(IdeaReadinessEventRow)
        .where(IdeaReadinessEventRow.idea_id == idea.id)
        .order_by(col(IdeaReadinessEventRow.created_at).desc())
    ).all()
    return [readiness_from_row(row) for row in rows]


def list_readiness(
    session: Session,
    access: AccessContext,
    project: str | None = None,
    tasks: Sequence[str] | None = None,
    latest_only: bool = True,
) -> list[ReadinessEvent]:
    """Readiness for a whole project, or for named tasks, in one call. `distinct on` keeps
    the newest event per task; the id tiebreaker matters because a backfill can mint several
    events sharing a timestamp."""
    if tasks:
        idea_ids = [require_idea_ref(session, access, reference).id for reference in tasks]
    else:
        scope = list_scope(session, access, project)
        query = select(IdeaRow.id)
        if scope is not None:
            query = query.where(col(IdeaRow.project_id).in_(scope))
        idea_ids = list(session.exec(query))
    if not idea_ids:
        return []

    query = select(IdeaReadinessEventRow).where(col(IdeaReadinessEventRow.idea_id).in_(idea_ids))
    if latest_only:
        query = query.distinct(col(IdeaReadinessEventRow.idea_id)).order_by(
            col(IdeaReadinessEventRow.idea_id),
            col(IdeaReadinessEventRow.created_at).desc(),
            col(IdeaReadinessEventRow.id).desc(),
        )
    else:
        query = query.order_by(
            col(IdeaReadinessEventRow.created_at).desc(), col(IdeaReadinessEventRow.id).desc()
        )
    events = [readiness_from_row(row) for row in session.exec(query)]
    if latest_only:
        events.sort(key=lambda event: (event.created_at, event.id), reverse=True)
    return events
