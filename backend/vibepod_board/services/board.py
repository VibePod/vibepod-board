"""Kanban board cards. A card is created when its idea becomes ready and mirrors the idea's
title, details, labels and repository."""

from datetime import datetime

from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, AdminAccess, assert_can_access_project
from vibepod_board.enums import BOARD_COLUMNS, BoardColumn
from vibepod_board.schemas import BoardCard, BoardColumns, now
from vibepod_board.services.common import (
    add_activity,
    card_details_for_idea,
    card_from_row,
    new_id,
    normalize_optional_text,
    require_card,
    require_project,
    transactional,
)
from vibepod_board.services.dependencies import decorate_card, decorate_cards
from vibepod_board.tables import BoardCardRow, IdeaRow

UNSET = object()


def _ordered(query):
    return query.order_by(col(BoardCardRow.updated_at).desc(), col(BoardCardRow.title))


def list_cards(
    session: Session, access: AccessContext, project_id: str | None = None
) -> list[BoardCard]:
    query = select(BoardCardRow)
    if project_id:
        project = require_project(session, project_id)
        assert_can_access_project(access, project.id)
        query = query.where(BoardCardRow.project_id == project.id)
    elif not isinstance(access, AdminAccess):
        if not access.project_ids:
            return []
        query = query.where(col(BoardCardRow.project_id).in_(access.project_ids))
    rows = session.exec(_ordered(query)).all()
    return decorate_cards(session, [card_from_row(row) for row in rows])


def board_columns(
    session: Session, access: AccessContext, project_id: str | None = None
) -> BoardColumns:
    grouped: dict[str, list[BoardCard]] = {column: [] for column in BOARD_COLUMNS}
    for card in list_cards(session, access, project_id):
        grouped[card.column].append(card)
    for cards in grouped.values():
        # Newest first, then by title, like the TS store's sortUpdatedDesc.
        cards.sort(key=lambda card: card.title)
        cards.sort(key=lambda card: card.updated_at, reverse=True)
    return BoardColumns(**grouped)


def ensure_card(session: Session, idea: IdeaRow, timestamp: datetime | None = None) -> BoardCardRow:
    """Creates the idea's card, or refreshes it from the idea when it already exists."""
    timestamp = timestamp or now()
    existing = session.exec(select(BoardCardRow).where(BoardCardRow.idea_id == idea.id)).first()
    if existing:
        existing.title = idea.title
        existing.details = card_details_for_idea(idea)
        existing.labels = list(idea.labels)
        existing.repository_local_path = idea.repository_local_path
        existing.repository_remote_url = idea.repository_remote_url
        existing.github_issue_url = idea.github_issue_url
        existing.github_issue_number = idea.github_issue_number
        existing.updated_at = timestamp
        session.flush()
        return existing

    card = BoardCardRow(
        id=new_id(),
        project_id=idea.project_id,
        idea_id=idea.id,
        title=idea.title,
        details=card_details_for_idea(idea),
        column_name=BoardColumn.READY,
        github_issue_url=idea.github_issue_url,
        github_issue_number=idea.github_issue_number,
        repository_local_path=idea.repository_local_path,
        repository_remote_url=idea.repository_remote_url,
        labels=list(idea.labels),
        readiness_score=idea.readiness_score,
        readiness_reason=idea.readiness_reason,
        readiness_evaluated_at=idea.readiness_evaluated_at,
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(card)
    session.flush()
    add_activity(session, "board.created", f"Created board card: {card.title}", timestamp)
    return card


def sync_card_from_idea(session: Session, idea: IdeaRow, timestamp: datetime) -> None:
    for card in session.exec(select(BoardCardRow).where(BoardCardRow.idea_id == idea.id)):
        card.title = idea.title
        card.details = card_details_for_idea(idea)
        card.labels = list(idea.labels)
        card.repository_local_path = idea.repository_local_path
        card.repository_remote_url = idea.repository_remote_url
        card.github_issue_url = idea.github_issue_url
        card.github_issue_number = idea.github_issue_number
        card.updated_at = timestamp
    session.flush()


@transactional
def update_card(
    session: Session,
    access: AccessContext,
    card_id: str,
    column: BoardColumn | None = None,
    branch_name: str | None | object = UNSET,
    details: str | None = None,
    repository_local_path: str | None | object = UNSET,
    repository_remote_url: str | None | object = UNSET,
) -> BoardCard:
    card = require_card(session, card_id)
    assert_can_access_project(access, card.project_id)
    if column is not None:
        card.column_name = column
    if isinstance(branch_name, str):
        card.branch_name = normalize_optional_text(branch_name)
    if details is not None:
        card.details = details.strip()
    if isinstance(repository_local_path, str):
        card.repository_local_path = normalize_optional_text(repository_local_path)
    if isinstance(repository_remote_url, str):
        card.repository_remote_url = normalize_optional_text(repository_remote_url)
    timestamp = now()
    card.updated_at = timestamp
    session.flush()
    decorated = decorate_card(session, card_from_row(card))
    add_activity(session, "board.updated", f"Updated board card: {card.title}", timestamp)
    return decorated


@transactional
def move_card(
    session: Session, access: AccessContext, card_id: str, column: BoardColumn
) -> BoardCard:
    card = require_card(session, card_id)
    assert_can_access_project(access, card.project_id)
    timestamp = now()
    card.column_name = column
    card.updated_at = timestamp
    session.flush()
    decorated = decorate_card(session, card_from_row(card))
    add_activity(
        session, "board.moved", f"Moved card to {BoardColumn(column)}: {card.title}", timestamp
    )
    return decorated
