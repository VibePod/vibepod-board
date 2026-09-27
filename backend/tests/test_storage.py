"""Port of tests/storage.test.ts (PostgresBoardStore) onto the Python service layer."""

from typing import Any

import pytest
from pydantic import BaseModel
from sqlmodel import Session

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import BoardColumn, DocumentKind, IdeaStatus
from vibepod_board.errors import BadRequest, Conflict
from vibepod_board.schemas import BoardCard
from vibepod_board.services.board import (
    LOCAL,
    board_columns,
    create_card_from_idea,
    move_card,
    update_card,
)
from vibepod_board.services.documents import create_document
from vibepod_board.services.ideas import (
    create_idea,
    list_ideas,
    mark_ready,
    set_board_availability,
    update_idea,
)
from vibepod_board.services.projects import create_project, list_projects, update_project
from vibepod_board.services.readiness import set_card_readiness
from vibepod_board.services.state import board_state

admin = admin_access("admin")


def assert_matches(model: BaseModel, expected: dict[str, Any]) -> None:
    """Like vitest's `toMatchObject`: every expected camelCase key has the expected value."""
    dumped = model.model_dump(mode="json")
    assert {key: dumped.get(key) for key in expected} == expected


def ready_card(session: Session, project_id: str) -> BoardCard:
    return board_columns(session, admin, project_id).ready[0]


def test_requires_unique_1_to_3_capital_letter_project_ids(session: Session) -> None:
    with pytest.raises(BadRequest, match="Project ID is required"):
        create_project(session, key=None, title="No key")  # type: ignore[arg-type]
    with pytest.raises(BadRequest, match="Project ID must be 1 to 3 capital letters"):
        create_project(session, key="app", title="Invalid")

    project = create_project(session, key="APP", title="App")
    assert project.key == "APP"

    with pytest.raises(Conflict, match="Project ID APP is already used"):
        create_project(session, key="APP", title="Duplicate")

    other_project = create_project(session, key="BL", title="Backlog")
    assert update_project(session, other_project.id, key="WEB").key == "WEB"
    with pytest.raises(Conflict, match="Project ID APP is already used"):
        update_project(session, other_project.id, key="APP")


def test_assigns_incrementing_task_numbers_per_project(session: Session) -> None:
    app = create_project(session, key="APP", title="App")
    api = create_project(session, key="API", title="API")

    first_app_task = create_idea(session, admin, project_id=app.id, title="First app task")
    second_app_task = create_idea(session, admin, project_id=app.id, title="Second app task")
    first_api_task = create_idea(session, admin, project_id=api.id, title="First API task")

    assert first_app_task.task_number == 1
    assert second_app_task.task_number == 2
    assert first_api_task.task_number == 1


def test_stores_tasks_inside_a_project(session: Session) -> None:
    project = create_project(
        session, key="LS", title="Launch site", summary="Coordinate launch work"
    )
    other_project = create_project(session, key="BL", title="Backlog")

    idea = create_idea(
        session,
        admin,
        project_id=project.id,
        title="Publish landing page",
        summary="Ship the first project page",
    )
    create_idea(session, admin, project_id=other_project.id, title="Unrelated backlog task")

    assert len(list_projects(session)) == 2
    assert [item.id for item in list_ideas(session, admin, project.id)] == [idea.id]


def test_stores_denied_tasks_for_rejected_ideas(session: Session) -> None:
    project = create_project(session, key="LS", title="Launch site")
    idea = create_idea(session, admin, project_id=project.id, title="Add confetti animation")

    denied = update_idea(session, admin, idea.id, status=IdeaStatus.DENIED)

    assert denied.status == "denied"
    assert list_ideas(session, admin, project.id)[0].status == "denied"


def test_refines_an_idea_when_details_and_acceptance_criteria_are_updated(
    session: Session,
) -> None:
    idea = create_idea(
        session,
        admin,
        title="Add GitHub sync",
        summary="Create GitHub issues from ready ideas",
        details="",
        labels=["github", "workflow"],
    )

    refined = update_idea(
        session,
        admin,
        idea.id,
        details="Create GitHub issues only when required fields are collected.",
        acceptance_criteria=["Ready ideas can be selected", "A board card is created"],
    )

    assert refined.status == "refining"
    assert refined.acceptance_criteria == [
        "Ready ideas can be selected",
        "A board card is created",
    ]


def test_uses_ready_as_the_flag_for_board_availability(session: Session) -> None:
    project = create_project(session, key="LS", title="Launch site")
    idea = create_idea(session, admin, project_id=project.id, title="Publish launch checklist")

    ready = mark_ready(session, admin, idea.id)
    assert ready.status == "ready"
    assert len(board_columns(session, admin, project.id).ready) == 1
    assert board_columns(session, admin, project.id).ready[0].idea_id == idea.id

    unavailable = set_board_availability(session, admin, idea.id, False)
    assert unavailable.status == "idea"
    assert len(board_columns(session, admin, project.id).ready) == 0


def test_moves_board_cards_between_any_columns_without_workflow_constraints(
    session: Session,
) -> None:
    project = create_project(session, key="LS", title="Launch site")
    idea = create_idea(session, admin, project_id=project.id, title="Publish launch checklist")
    mark_ready(session, admin, idea.id)
    card = ready_card(session, project.id)

    assert move_card(session, admin, card.id, BoardColumn.DONE).column == "done"
    assert move_card(session, admin, card.id, BoardColumn.PLANNED).column == "planned"


def test_stores_the_implementation_branch_name_on_board_cards(session: Session) -> None:
    project = create_project(session, key="CLI", title="CLI")
    idea = create_idea(session, admin, project_id=project.id, title="Persist lifecycle state")
    mark_ready(session, admin, idea.id)
    card = ready_card(session, project.id)

    updated = update_card(session, admin, card.id, branch_name="vp-task-lifecycle-state")

    assert updated.branch_name == "vp-task-lifecycle-state"
    assert ready_card(session, project.id).branch_name == "vp-task-lifecycle-state"


def test_copies_repository_metadata_from_ready_tasks_onto_board_cards(session: Session) -> None:
    project = create_project(session, key="CLI", title="CLI")
    idea = create_idea(
        session,
        admin,
        project_id=project.id,
        title="Persist lifecycle state",
        details="Write lifecycle files in the CLI repository.",
        repository_local_path="/workspace/vibepod-cli",
        repository_remote_url="git@github.com:vibepod/vibepod-cli.git",
    )

    mark_ready(session, admin, idea.id)
    card = ready_card(session, project.id)

    assert_matches(
        card,
        {
            "ideaId": idea.id,
            "details": "Write lifecycle files in the CLI repository.",
            "repositoryLocalPath": "/workspace/vibepod-cli",
            "repositoryRemoteUrl": "git@github.com:vibepod/vibepod-cli.git",
        },
    )


def test_syncs_edited_task_details_and_repository_metadata_to_an_existing_board_card(
    session: Session,
) -> None:
    project = create_project(session, key="CLI", title="CLI")
    idea = create_idea(
        session,
        admin,
        project_id=project.id,
        title="Persist lifecycle state",
        details="Initial implementation details",
        repository_local_path="/workspace/old",
        repository_remote_url="git@github.com:vibepod/old.git",
    )
    mark_ready(session, admin, idea.id)
    card = ready_card(session, project.id)
    update_card(session, admin, card.id, branch_name="vp-task-lifecycle-state")

    updated = update_idea(
        session,
        admin,
        idea.id,
        details="Updated implementation details",
        repository_local_path="/workspace/vibepod-cli",
        repository_remote_url="git@github.com:vibepod/vibepod-cli.git",
    )
    updated_card = ready_card(session, project.id)

    assert_matches(
        updated,
        {
            "repositoryLocalPath": "/workspace/vibepod-cli",
            "repositoryRemoteUrl": "git@github.com:vibepod/vibepod-cli.git",
        },
    )
    assert_matches(
        updated_card,
        {
            "branchName": "vp-task-lifecycle-state",
            "details": "Updated implementation details",
            "repositoryLocalPath": "/workspace/vibepod-cli",
            "repositoryRemoteUrl": "git@github.com:vibepod/vibepod-cli.git",
        },
    )


def test_updates_board_card_implementation_details_and_repository_metadata_directly(
    session: Session,
) -> None:
    project = create_project(session, key="CLI", title="CLI")
    idea = create_idea(session, admin, project_id=project.id, title="Persist lifecycle state")
    mark_ready(session, admin, idea.id)
    card = ready_card(session, project.id)

    updated = update_card(
        session,
        admin,
        card.id,
        details="Implemented in vibepod-cli",
        repository_local_path="/workspace/vibepod-cli",
        repository_remote_url="git@github.com:vibepod/vibepod-cli.git",
    )

    assert_matches(
        updated,
        {
            "details": "Implemented in vibepod-cli",
            "repositoryLocalPath": "/workspace/vibepod-cli",
            "repositoryRemoteUrl": "git@github.com:vibepod/vibepod-cli.git",
        },
    )


def test_stores_execution_plan_documents_linked_to_ideas_and_board_cards(
    session: Session,
) -> None:
    idea = create_idea(session, admin, title="MCP bridge", labels=["mcp"])
    card = create_card_from_idea(session, admin, mark_ready(session, admin, idea.id).id, LOCAL)

    document = create_document(
        session,
        admin,
        title="MCP Bridge Execution Plan",
        kind=DocumentKind.EXECUTION_PLAN,
        content="Build MCP tools for reading and updating the board.",
        linked_idea_ids=[idea.id],
        linked_card_ids=[card.id],
    )

    assert document.kind == "execution_plan"
    assert document.linked_idea_ids == [idea.id]
    assert document.linked_card_ids == [card.id]


def test_exports_scoped_state_for_admins_and_project_tokens(session: Session) -> None:
    project = create_project(session, key="LS", title="Launch site")
    other_project = create_project(session, key="BL", title="Backlog")
    create_idea(session, admin, project_id=project.id, title="Scoped task")
    create_idea(session, admin, project_id=other_project.id, title="Hidden task")

    full_state = board_state(session, admin)
    scoped_state = board_state(session, token_access("token-1", [project.id]))

    assert sorted(item.id for item in full_state.projects) == sorted([other_project.id, project.id])
    assert [item.id for item in scoped_state.projects] == [project.id]
    assert [item.title for item in scoped_state.ideas] == ["Scoped task"]


def test_sets_readiness_on_a_board_card_without_bumping_updated_at(session: Session) -> None:
    project = create_project(session, key="APP", title="App")
    idea = create_idea(session, admin, project_id=project.id, title="Scored card")
    mark_ready(session, admin, idea.id)
    card = ready_card(session, project.id)

    scored = set_card_readiness(
        session, admin, card.id, score=7, reason="Clear scope, acceptance criteria present"
    )

    assert scored.readiness_score == 7
    assert scored.readiness_reason == "Clear scope, acceptance criteria present"
    assert scored.readiness_evaluated_at is not None
    assert scored.updated_at == card.updated_at
    assert scored.readiness_evaluated_at >= scored.updated_at


def test_rejects_invalid_readiness_scores_and_empty_reasons(session: Session) -> None:
    project = create_project(session, key="APP", title="App")
    idea = create_idea(session, admin, project_id=project.id, title="Card")
    mark_ready(session, admin, idea.id)
    card = ready_card(session, project.id)

    for score in (0, 11, 6.5):
        with pytest.raises(BadRequest, match="Readiness score must be an integer from 1 to 10"):
            set_card_readiness(session, admin, card.id, score=score, reason="r")
    with pytest.raises(BadRequest, match="Readiness reason is required"):
        set_card_readiness(session, admin, card.id, score=5, reason="  ")


def test_marks_readiness_stale_after_a_content_edit_but_not_after_re_evaluation(
    session: Session,
) -> None:
    project = create_project(session, key="APP", title="App")
    idea = create_idea(session, admin, project_id=project.id, title="Card")
    mark_ready(session, admin, idea.id)
    card = ready_card(session, project.id)

    scored = set_card_readiness(session, admin, card.id, score=4, reason="No repo set")
    edited = update_card(session, admin, card.id, details="Now with repo info")

    assert scored.readiness_evaluated_at is not None
    assert edited.updated_at > scored.readiness_evaluated_at
    assert edited.readiness_score == 4
    assert edited.readiness_reason == "No repo set"
