import pytest
from conftest import login
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import DocumentKind
from vibepod_board.errors import NotFound
from vibepod_board.services import (
    activity,
    board,
    dependencies,
    documents,
    ideas,
    projects,
    readiness,
)
from vibepod_board.tables import IdeaReadinessEventRow, TaskDependencyRow

ADMIN = admin_access("admin")


@pytest.fixture
def project(session: Session):
    return projects.create_project(session, "APP", "App")


def test_deletes_the_task_with_its_card_edges_history_and_document_links(
    session: Session, project
) -> None:
    schema = ideas.create_idea(session, ADMIN, title="Schema", project_id=project.id)
    api = ideas.create_idea(
        session, ADMIN, title="API", project_id=project.id, depends_on=[schema.id]
    )
    ideas.mark_ready(session, ADMIN, schema.id)
    readiness.set_idea_readiness(session, ADMIN, schema.id, 8, "Clear")
    card = board.board_columns(session, ADMIN, project.id).ready[0]
    plan = documents.create_document(
        session,
        ADMIN,
        title="Plan",
        project_id=project.id,
        kind=DocumentKind.EXECUTION_PLAN,
        linked_idea_ids=[schema.id, api.id],
        linked_card_ids=[card.id],
    )

    deleted = ideas.delete_idea(session, ADMIN, schema.id)

    assert deleted.model_dump(mode="json") == {
        "id": schema.id,
        "taskId": "APP-1",
        "dependents": [api.id],
    }
    assert [idea.title for idea in ideas.list_ideas(session, ADMIN, project.id)] == ["API"]
    assert board.board_columns(session, ADMIN, project.id).ready == []
    [remaining] = ideas.list_ideas(session, ADMIN, project.id)
    assert (remaining.depends_on, remaining.blocked_by) == ([], [])
    assert session.exec(select(TaskDependencyRow)).all() == []
    assert session.exec(select(IdeaReadinessEventRow)).all() == []
    [document] = documents.list_documents(session, ADMIN, project.id)
    assert document.id == plan.id
    assert (document.linked_idea_ids, document.linked_card_ids) == ([api.id], [])
    assert activity.list_activity(session, ADMIN)[0].message == "Deleted task APP-1: Schema"


def test_task_numbers_are_not_reused_after_a_delete(session: Session, project) -> None:
    ideas.create_idea(session, ADMIN, title="One", project_id=project.id)
    two = ideas.create_idea(session, ADMIN, title="Two", project_id=project.id)
    # Deleting the highest number is the case a MAX(task_number) + 1 allocator gets wrong.
    ideas.delete_idea(session, ADMIN, two.id)

    three = ideas.create_idea(session, ADMIN, title="Three", project_id=project.id)

    assert three.task_number == 3


def test_a_blocker_delete_unblocks_its_dependents(session: Session, project) -> None:
    blocker = ideas.create_idea(session, ADMIN, title="Blocker", project_id=project.id)
    task = ideas.create_idea(session, ADMIN, title="Task", project_id=project.id)
    dependencies.add_dependency(session, ADMIN, task.id, blocker.id)

    ideas.delete_idea(session, ADMIN, blocker.id)

    order = ideas.work_order(session, ADMIN, project.id)
    assert [(item.title, item.is_actionable) for item in order.items] == [("Task", True)]


def test_deletes_stay_inside_the_token_scope(session: Session, project) -> None:
    other = projects.create_project(session, "OTH", "Other")
    theirs = ideas.create_idea(session, ADMIN, title="Off limits", project_id=other.id)

    # A task outside the token's projects reads as missing, so scope cannot be probed.
    with pytest.raises(NotFound, match=f"Task not found: {theirs.id}"):
        ideas.delete_idea(session, token_access("token-1", [project.id]), theirs.id)
    with pytest.raises(NotFound, match="Task not found: missing"):
        ideas.delete_idea(session, ADMIN, "missing")
    assert len(ideas.list_ideas(session, ADMIN, other.id)) == 1


def test_rest_delete(client: TestClient) -> None:
    agent = login(client)
    created = agent.post("/api/ideas", json={"title": "Remove me"}).json()["item"]

    response = agent.delete(f"/api/ideas/{created['id']}")
    assert response.status_code == 200
    assert response.json()["dependents"] == []
    assert agent.get("/api/ideas").json()["items"] == []
    assert agent.delete(f"/api/ideas/{created['id']}").status_code == 404

    anonymous = TestClient(client.app)
    assert anonymous.delete(f"/api/ideas/{created['id']}").status_code == 401
