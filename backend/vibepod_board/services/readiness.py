"""LLM-evaluated readiness scores. The idea is the source of truth: a score is mirrored onto
its board card and appended to the history. Neither write bumps `updated_at`, so a later
content edit marks the score stale."""

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
    require_card,
    require_idea,
    transactional,
)
from vibepod_board.services.dependencies import decorate_card, decorate_idea
from vibepod_board.tables import BoardCardRow, IdeaReadinessEventRow


@transactional
def set_idea_readiness(
    session: Session, access: AccessContext, idea_id: str, score: Any, reason: str
) -> Idea:
    score, reason = normalize_readiness(score, reason)
    idea = require_idea(session, idea_id)
    assert_can_access_project(access, idea.project_id)
    timestamp = now()
    idea.readiness_score = score
    idea.readiness_reason = reason
    idea.readiness_evaluated_at = timestamp
    session.execute(
        update(BoardCardRow)
        .where(col(BoardCardRow.idea_id) == idea_id)
        .values(readiness_score=score, readiness_reason=reason, readiness_evaluated_at=timestamp)
    )
    session.add(
        IdeaReadinessEventRow(
            id=new_id(), idea_id=idea_id, score=score, reason=reason, created_at=timestamp
        )
    )
    session.flush()
    decorated = decorate_idea(session, idea)
    add_activity(session, "idea.readiness", f"Set readiness {score}/10: {idea.title}", timestamp)
    return decorated


def set_card_readiness(
    session: Session, access: AccessContext, card_id: str, score: Any, reason: str
) -> BoardCard:
    card = require_card(session, card_id)
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
    idea = require_idea(session, idea_id)
    assert_can_access_project(access, idea.project_id)
    rows = session.exec(
        select(IdeaReadinessEventRow)
        .where(IdeaReadinessEventRow.idea_id == idea_id)
        .order_by(col(IdeaReadinessEventRow.created_at).desc())
    ).all()
    return [readiness_from_row(row) for row in rows]
