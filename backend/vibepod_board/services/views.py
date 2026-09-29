"""How much of a record a response carries: `ref` (id, key, updatedAt), `compact` (identity,
state and relationships, with dependencies named by key and free text reduced to its size) or
`full` (the whole record, plus its key)."""

from collections.abc import Iterable
from enum import StrEnum

from sqlmodel import Session

from vibepod_board.schemas import (
    BoardCard,
    CompactBoardCard,
    CompactDocument,
    CompactIdea,
    EntityRef,
    Idea,
    KeyedBoardCard,
    KeyedIdea,
    PlanDocument,
)
from vibepod_board.services.references import task_keys


class View(StrEnum):
    REF = "ref"
    COMPACT = "compact"
    FULL = "full"


def _referenced_ids(ideas: Iterable[Idea], cards: Iterable[BoardCard]) -> list[str]:
    """Every task id a response mentions, including dependency ids that are not themselves
    in the result, so keys resolve everywhere they appear."""
    ids: dict[str, None] = {}
    for idea in ideas:
        ids.update(dict.fromkeys([idea.id, *idea.depends_on, *idea.blocked_by]))
    for card in cards:
        if card.idea_id:
            ids[card.idea_id] = None
        ids.update(dict.fromkeys(card.blocked_by))
    return list(ids)


def _named(keys: dict[str, str], ids: list[str]) -> list[str]:
    return [keys.get(task_id, task_id) for task_id in ids]


def project_ideas(
    session: Session,
    ideas: list[Idea],
    view: View,
    cards: list[BoardCard] | None = None,
) -> list[KeyedIdea | CompactIdea | EntityRef]:
    """`cards` lets a compact task report its board column."""
    keys = task_keys(session, _referenced_ids(ideas, cards or []))
    if view == View.FULL:
        return [KeyedIdea(**dict(idea), key=keys.get(idea.id)) for idea in ideas]
    if view == View.REF:
        return [
            EntityRef(id=idea.id, key=keys.get(idea.id), updated_at=idea.updated_at)
            for idea in ideas
        ]
    columns = {card.idea_id: card.column for card in cards or [] if card.idea_id}
    return [
        CompactIdea(
            id=idea.id,
            key=keys.get(idea.id),
            project_id=idea.project_id,
            task_number=idea.task_number,
            title=idea.title,
            status=idea.status,
            labels=idea.labels,
            column=columns.get(idea.id),
            assignee=idea.assignee,
            readiness_score=idea.readiness_score,
            depends_on=_named(keys, idea.depends_on),
            blocked_by=_named(keys, idea.blocked_by),
            details_length=len(idea.details),
            acceptance_criteria_count=len(idea.acceptance_criteria),
            updated_at=idea.updated_at,
        )
        for idea in ideas
    ]


def project_cards(
    session: Session, cards: list[BoardCard], view: View
) -> list[KeyedBoardCard | CompactBoardCard | EntityRef]:
    keys = task_keys(session, _referenced_ids([], cards))

    def key(card: BoardCard) -> str | None:
        return keys.get(card.idea_id) if card.idea_id else None

    if view == View.FULL:
        return [KeyedBoardCard(**dict(card), key=key(card)) for card in cards]
    if view == View.REF:
        return [EntityRef(id=card.id, key=key(card), updated_at=card.updated_at) for card in cards]
    return [
        CompactBoardCard(
            id=card.id,
            key=key(card),
            idea_id=card.idea_id,
            project_id=card.project_id,
            title=card.title,
            column=card.column,
            branch_name=card.branch_name,
            labels=card.labels,
            blocked_by=_named(keys, card.blocked_by),
            assignee=card.assignee,
            claimed_at=card.claimed_at,
            attempts=card.attempts or None,
            blocked_reason=card.blocked_reason,
            question=card.question,
            readiness_score=card.readiness_score,
            details_length=len(card.details),
            updated_at=card.updated_at,
        )
        for card in cards
    ]


def project_documents(
    documents: list[PlanDocument], view: View
) -> list[PlanDocument | CompactDocument]:
    if view == View.FULL:
        return list(documents)
    return [
        CompactDocument(
            id=document.id,
            project_id=document.project_id,
            title=document.title,
            kind=document.kind,
            linked_idea_ids=document.linked_idea_ids,
            content_length=len(document.content),
            updated_at=document.updated_at,
        )
        for document in documents
    ]
