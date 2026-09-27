"""Kanban board cards. A card is created when its idea becomes ready and mirrors the idea's
title, details, labels and repository.

A card finished in the done column can be archived: it leaves the board but keeps its task,
dependencies and history, and it still counts as done. Syncing from its task refreshes an
archived card's content without bringing it back; only `unarchive_card` does that."""

from datetime import datetime

from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, AdminAccess, assert_can_access_project
from vibepod_board.enums import BOARD_COLUMNS, BoardColumn
from vibepod_board.errors import Conflict
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
    session: Session,
    access: AccessContext,
    project_id: str | None = None,
    archived: bool | None = None,
) -> list[BoardCard]:
    """Cards the caller may see. `archived` narrows to archived (True) or active (False)
    cards; by default both are returned, so exports and the work order see every card."""
    query = select(BoardCardRow)
    if project_id:
        project = require_project(session, project_id)
        assert_can_access_project(access, project.id)
        query = query.where(BoardCardRow.project_id == project.id)
    elif not isinstance(access, AdminAccess):
        if not access.project_ids:
            return []
        query = query.where(col(BoardCardRow.project_id).in_(access.project_ids))
    if archived is True:
        query = query.where(col(BoardCardRow.archived_at).is_not(None))
    elif archived is False:
        query = query.where(col(BoardCardRow.archived_at).is_(None))
    rows = session.exec(_ordered(query)).all()
    return decorate_cards(session, [card_from_row(row) for row in rows])


def list_archived_cards(
    session: Session, access: AccessContext, project_id: str | None = None
) -> list[BoardCard]:
    """Archived cards, most recently archived first."""
    cards = list_cards(session, access, project_id, archived=True)
    return sorted(cards, key=lambda card: card.archived_at or card.updated_at, reverse=True)


def board_columns(
    session: Session, access: AccessContext, project_id: str | None = None
) -> BoardColumns:
    grouped: dict[str, list[BoardCard]] = {column: [] for column in BOARD_COLUMNS}
    for card in list_cards(session, access, project_id, archived=False):
        grouped[card.column].append(card)
    for cards in grouped.values():
        # Newest first, then by title, like the TS store's sortUpdatedDesc.
        cards.sort(key=lambda card: card.title)
        cards.sort(key=lambda card: card.updated_at, reverse=True)
    return BoardColumns(**grouped)


def ensure_card(session: Session, idea: IdeaRow, timestamp: datetime | None = None) -> BoardCardRow:
    """Creates the idea's card, or refreshes it from the idea when it already exists. An
    archived card stays archived."""
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
    """Mirrors the idea onto its card; the card's column and archive state are left alone."""
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


ARCHIVED_MESSAGE = "Board card is archived; unarchive it first"


def _require_active_card(session: Session, access: AccessContext, card_id: str) -> BoardCardRow:
    card = require_card(session, card_id)
    assert_can_access_project(access, card.project_id)
    if card.archived_at is not None:
        raise Conflict(ARCHIVED_MESSAGE)
    return card


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
    card = _require_active_card(session, access, card_id)
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
    card = _require_active_card(session, access, card_id)
    timestamp = now()
    card.column_name = column
    card.updated_at = timestamp
    session.flush()
    decorated = decorate_card(session, card_from_row(card))
    add_activity(
        session, "board.moved", f"Moved card to {BoardColumn(column)}: {card.title}", timestamp
    )
    return decorated


def _archive(session: Session, card: BoardCardRow, timestamp: datetime) -> None:
    card.archived_at = timestamp
    card.updated_at = timestamp
    session.flush()
    add_activity(session, "board.archived", f"Archived card: {card.title}", timestamp)


@transactional
def archive_card(session: Session, access: AccessContext, card_id: str) -> BoardCard:
    card = require_card(session, card_id)
    assert_can_access_project(access, card.project_id)
    if card.archived_at is not None:
        raise Conflict("Board card is already archived")
    if card.column_name != BoardColumn.DONE:
        raise Conflict("Only cards in the done column can be archived")
    _archive(session, card, now())
    return decorate_card(session, card_from_row(card))


@transactional
def archive_done_cards(session: Session, access: AccessContext, project_id: str) -> list[BoardCard]:
    """Archives every active card in the project's done column."""
    project = require_project(session, project_id)
    assert_can_access_project(access, project.id)
    rows = session.exec(
        _ordered(
            select(BoardCardRow).where(
                BoardCardRow.project_id == project.id,
                BoardCardRow.column_name == BoardColumn.DONE,
                col(BoardCardRow.archived_at).is_(None),
            )
        )
    ).all()
    timestamp = now()
    for card in rows:
        _archive(session, card, timestamp)
    return decorate_cards(session, [card_from_row(row) for row in rows])


@transactional
def unarchive_card(session: Session, access: AccessContext, card_id: str) -> BoardCard:
    """Puts an archived card back on the board, in the done column it was archived from."""
    card = require_card(session, card_id)
    assert_can_access_project(access, card.project_id)
    if card.archived_at is None:
        raise Conflict("Board card is not archived")
    timestamp = now()
    card.archived_at = None
    card.column_name = BoardColumn.DONE
    card.updated_at = timestamp
    session.flush()
    add_activity(session, "board.unarchived", f"Restored archived card: {card.title}", timestamp)
    return decorate_card(session, card_from_row(card))
