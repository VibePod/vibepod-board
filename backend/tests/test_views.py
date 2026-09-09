"""Views: ref, compact and full projections of tasks, cards and documents, and single reads."""

import json

import pytest
from sqlmodel import Session

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import BoardColumn
from vibepod_board.errors import NotFound
from vibepod_board.schemas import format_timestamp
from vibepod_board.services import board, documents, ideas, projects, readiness
from vibepod_board.services.views import View, project_cards, project_documents, project_ideas

ADMIN = admin_access("admin")
HOLDER = "Claude::Subagent101::Worktree12"


@pytest.fixture
def vp(session: Session):
    return projects.create_project(session, "VP", "VibePod")


@pytest.fixture
def pair(session: Session, vp):
    """VP-1 blocks VP-2; VP-2 is on the board in progress with a branch and a score."""
    blocker = ideas.create_idea(session, ADMIN, title="Blocker", project_id=vp.id)
    task = ideas.create_idea(
        session,
        ADMIN,
        title="Collapse the two-step write",
        project_id=vp.id,
        details="x" * 2048,
        labels=["api"],
        acceptance_criteria=["One call", "Card exists"],
        depends_on=[blocker.id],
    )
    ideas.mark_ready(session, ADMIN, task.id)
    readiness.set_idea_readiness(session, ADMIN, task.id, 8, "Scope is clear")
    card = board.update_card(
        session, ADMIN, "VP-2", column=BoardColumn.IN_PROGRESS, branch_name="vp-2-collapse"
    )
    return blocker, ideas.get_idea(session, ADMIN, task.id), card


def dump(model) -> dict:
    return model.model_dump(mode="json")


def test_reduces_a_task_to_its_reference(session: Session, pair) -> None:
    _, task, _ = pair
    [ref] = project_ideas(session, [task], View.REF)
    assert dump(ref) == {
        "id": task.id,
        "key": "VP-2",
        "updatedAt": format_timestamp(task.updated_at),
    }


def test_keeps_state_and_relationships_but_no_free_text_in_a_compact_task(
    session: Session, pair
) -> None:
    _, task, card = pair
    [compact] = project_ideas(session, [task], View.COMPACT, [card])
    assert dump(compact) == {
        "id": task.id,
        "key": "VP-2",
        "projectId": task.project_id,
        "taskNumber": 2,
        "title": "Collapse the two-step write",
        "status": "ready",
        "labels": ["api"],
        "column": "in_progress",
        "readinessScore": 8,
        "dependsOn": ["VP-1"],
        "blockedBy": ["VP-1"],
        "detailsLength": 2048,
        "acceptanceCriteriaCount": 2,
        "updatedAt": format_timestamp(task.updated_at),
    }
    text = json.dumps(dump(compact))
    assert "Scope is clear" not in text
    assert "xxxx" not in text


def test_omits_the_column_without_cards(session: Session, pair) -> None:
    _, task, _ = pair
    [compact] = project_ideas(session, [task], View.COMPACT)
    assert "column" not in dump(compact)


def test_full_view_is_the_whole_record_plus_its_key(session: Session, pair) -> None:
    _, task, _ = pair
    [full] = project_ideas(session, [task], View.FULL)
    assert dump(full) == {**dump(task), "key": "VP-2"}


def test_projects_a_card_with_its_task_key(session: Session, pair) -> None:
    _, task, card = pair
    card = board.get_card(session, ADMIN, card.id)
    [compact] = project_cards(session, [card], View.COMPACT)
    assert dump(compact) == {
        "id": card.id,
        "key": "VP-2",
        "ideaId": task.id,
        "projectId": task.project_id,
        "title": "Collapse the two-step write",
        "column": "in_progress",
        "branchName": "vp-2-collapse",
        "labels": ["api"],
        "blockedBy": ["VP-1"],
        "readinessScore": 8,
        "detailsLength": 2048,
        "updatedAt": format_timestamp(card.updated_at),
    }
    [ref] = project_cards(session, [card], View.REF)
    assert dump(ref) == {
        "id": card.id,
        "key": "VP-2",
        "updatedAt": format_timestamp(card.updated_at),
    }
    [full] = project_cards(session, [card], View.FULL)
    assert dump(full) == {**dump(card), "key": "VP-2"}


def test_carries_the_assignee_into_compact_views_and_omits_it_when_free(
    session: Session, pair
) -> None:
    _, task, card = pair
    assert "assignee" not in dump(project_ideas(session, [task], View.COMPACT)[0])
    assert "assignee" not in dump(project_cards(session, [card], View.COMPACT)[0])

    held = ideas.update_idea(session, ADMIN, task.id, assignee=HOLDER)
    held_card = board.get_card(session, ADMIN, card.id)
    assert dump(project_ideas(session, [held], View.COMPACT)[0])["assignee"] == HOLDER
    assert dump(project_cards(session, [held_card], View.COMPACT)[0])["assignee"] == HOLDER


def test_drops_document_content_and_keeps_its_length(session: Session, pair, vp) -> None:
    _, task, card = pair
    plan = documents.create_document(
        session,
        ADMIN,
        title="Execution plan",
        project_id=vp.id,
        content="z" * 512,
        linked_idea_ids=[task.id],
        linked_card_ids=[card.id],
    )
    [compact] = project_documents([plan], View.COMPACT)
    assert dump(compact) == {
        "id": plan.id,
        "projectId": vp.id,
        "title": "Execution plan",
        "kind": "execution_plan",
        "linkedIdeaIds": [task.id],
        "contentLength": 512,
        "updatedAt": format_timestamp(plan.updated_at),
    }
    assert project_documents([plan], View.FULL) == [plan]


# --- single reads ---------------------------------------------------------------------


def test_returns_a_decorated_task_by_key(session: Session, pair) -> None:
    blocker, task, _ = pair
    fetched = ideas.get_idea(session, ADMIN, "VP-2")
    assert (fetched.id, fetched.depends_on, fetched.blocked_by) == (
        task.id,
        [blocker.id],
        [blocker.id],
    )
    assert ideas.get_idea(session, ADMIN, blocker.id).blocks == [task.id]


def test_returns_a_decorated_card_by_task_key(session: Session, pair) -> None:
    blocker, task, _ = pair
    card = board.get_card(session, ADMIN, "VP-2")
    assert (card.idea_id, card.blocked_by) == (task.id, [blocker.id])


def test_single_reads_refuse_records_outside_the_callers_projects(session: Session, vp) -> None:
    other = projects.create_project(session, "OPS", "Operations")
    foreign = ideas.create_idea(session, ADMIN, title="Hidden", project_id=other.id)
    ideas.mark_ready(session, ADMIN, foreign.id)
    scoped = token_access("token-1", [vp.id])

    with pytest.raises(NotFound, match=f"Task not found: {foreign.id}"):
        ideas.get_idea(session, scoped, foreign.id)
    with pytest.raises(NotFound, match="Board card not found: OPS-1"):
        board.get_card(session, scoped, "OPS-1")
    with pytest.raises(NotFound, match=r"Task not found: VP-999 \(project VP, task 999\)"):
        ideas.get_idea(session, ADMIN, "VP-999")
