"""Port of tests/board-store-readiness.test.ts.

The TS file exercises the JSON-file `BoardStore`, which is not being ported. The same
readiness rules hold for `PostgresBoardStore` (setCardReadiness / setIdeaReadiness /
listIdeaReadiness), so every case is ported against the Python services instead: the idea is
the source of truth, the score is mirrored onto its card, neither write bumps `updated_at`,
history lists newest-first, and scoring a linked card redirects to its idea.
"""

import pytest
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.errors import BadRequest
from vibepod_board.schemas import BoardCard, Idea
from vibepod_board.services import board, ideas, projects, readiness

ADMIN = admin_access("admin")


def create_card(session: Session) -> BoardCard:
    project = projects.create_project(session, key="APP", title="App")
    idea = ideas.create_idea(session, ADMIN, project_id=project.id, title="Card")
    ideas.mark_ready(session, ADMIN, idea.id)
    return board.board_columns(session, ADMIN, project.id).ready[0]


def find_idea(session: Session, project_id: str, idea_id: str) -> Idea:
    return next(i for i in ideas.list_ideas(session, ADMIN, project_id) if i.id == idea_id)


def test_sets_readiness_without_bumping_updated_at_and_keeps_it_across_edits(
    session: Session,
) -> None:
    card = create_card(session)

    scored = readiness.set_card_readiness(
        session, ADMIN, card.id, score=9, reason="Fully specified"
    )
    assert scored.readiness_score == 9
    assert scored.readiness_reason == "Fully specified"
    assert scored.readiness_evaluated_at is not None
    assert scored.updated_at == card.updated_at

    edited = board.update_card(session, ADMIN, card.id, details="changed")
    assert edited.readiness_score == 9


def test_validates_score_and_reason(session: Session) -> None:
    card = create_card(session)

    with pytest.raises(BadRequest, match="Readiness score must be an integer from 1 to 10"):
        readiness.set_card_readiness(session, ADMIN, card.id, score=0, reason="r")
    with pytest.raises(BadRequest, match="Readiness reason is required"):
        readiness.set_card_readiness(session, ADMIN, card.id, score=5, reason=" ")


def test_sets_idea_readiness_and_mirrors_it_onto_the_linked_card_without_bumping_updated_at(
    session: Session,
) -> None:
    project = projects.create_project(session, key="IDR", title="Idea readiness")
    idea = ideas.create_idea(session, ADMIN, project_id=project.id, title="Scored idea")
    ideas.mark_ready(session, ADMIN, idea.id)
    card_before = board.board_columns(session, ADMIN, project.id).ready[0]
    idea_before = find_idea(session, project.id, idea.id)

    scored = readiness.set_idea_readiness(session, ADMIN, idea.id, score=7, reason="Clear scope")
    assert scored.readiness_score == 7
    assert scored.readiness_reason == "Clear scope"
    assert scored.readiness_evaluated_at is not None
    assert scored.updated_at == idea_before.updated_at

    card = board.board_columns(session, ADMIN, project.id).ready[0]
    assert card.readiness_score == 7
    assert card.readiness_reason == "Clear scope"
    assert card.updated_at == card_before.updated_at


def test_validates_idea_readiness_score_and_reason(session: Session) -> None:
    project = projects.create_project(session, key="IDV", title="Idea validate")
    idea = ideas.create_idea(session, ADMIN, project_id=project.id, title="Idea")

    with pytest.raises(BadRequest, match="Readiness score must be an integer from 1 to 10"):
        readiness.set_idea_readiness(session, ADMIN, idea.id, score=0, reason="r")
    with pytest.raises(BadRequest, match="Readiness reason is required"):
        readiness.set_idea_readiness(session, ADMIN, idea.id, score=5, reason=" ")


def test_redirects_card_scoring_to_the_linked_idea(session: Session) -> None:
    project = projects.create_project(session, key="RDR", title="Redirect")
    idea = ideas.create_idea(session, ADMIN, project_id=project.id, title="Linked")
    ideas.mark_ready(session, ADMIN, idea.id)
    card = board.board_columns(session, ADMIN, project.id).ready[0]

    scored_card = readiness.set_card_readiness(session, ADMIN, card.id, score=4, reason="Some risk")
    assert scored_card.readiness_score == 4

    updated_idea = find_idea(session, project.id, idea.id)
    assert updated_idea.readiness_score == 4
    assert updated_idea.readiness_reason == "Some risk"


def test_appends_a_readiness_event_on_every_call_and_lists_newest_first(
    session: Session,
) -> None:
    project = projects.create_project(session, key="HST", title="History")
    idea = ideas.create_idea(session, ADMIN, project_id=project.id, title="Tracked")

    readiness.set_idea_readiness(session, ADMIN, idea.id, score=4, reason="Rough")
    readiness.set_idea_readiness(session, ADMIN, idea.id, score=4, reason="Rough")
    readiness.set_idea_readiness(session, ADMIN, idea.id, score=8, reason="Refined")

    events = readiness.list_idea_readiness(session, ADMIN, idea.id)
    assert len(events) == 3
    assert events[0].score == 8
    assert events[0].reason == "Refined"
    assert events[1].score == 4
    assert all(e.idea_id == idea.id for e in events)
    assert events[0].created_at >= events[2].created_at

    updated = find_idea(session, project.id, idea.id)
    assert updated.readiness_score == 8


def test_scopes_history_to_the_idea(session: Session) -> None:
    project = projects.create_project(session, key="SCP", title="Scope")
    a = ideas.create_idea(session, ADMIN, project_id=project.id, title="A")
    b = ideas.create_idea(session, ADMIN, project_id=project.id, title="B")
    readiness.set_idea_readiness(session, ADMIN, a.id, score=5, reason="a")
    readiness.set_idea_readiness(session, ADMIN, b.id, score=6, reason="b")

    assert len(readiness.list_idea_readiness(session, ADMIN, a.id)) == 1
    assert readiness.list_idea_readiness(session, ADMIN, a.id)[0].reason == "a"


def test_carries_idea_readiness_onto_a_card_created_at_promotion_time(session: Session) -> None:
    project = projects.create_project(session, key="CRY", title="Carry")
    idea = ideas.create_idea(session, ADMIN, project_id=project.id, title="Scored then promoted")
    readiness.set_idea_readiness(session, ADMIN, idea.id, score=8, reason="Ready to ship")

    ideas.mark_ready(session, ADMIN, idea.id)
    card = board.board_columns(session, ADMIN, project.id).ready[0]
    assert card.readiness_score == 8
    assert card.readiness_reason == "Ready to ship"
    assert card.readiness_evaluated_at is not None
