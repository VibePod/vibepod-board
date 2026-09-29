"""Task assignees: the task owns the holder, its card mirrors it, and lists filter by it."""

import pytest
from conftest import login
from fastapi.testclient import TestClient
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.enums import BoardColumn
from vibepod_board.errors import Conflict
from vibepod_board.services import board, ideas, projects, transfer
from vibepod_board.services.board import CardFilter
from vibepod_board.services.ideas import IdeaFilter

ADMIN = admin_access("admin")
AGENT = "Claude::Subagent101::Worktree12"
OTHER = "Codex::Session3"


@pytest.fixture
def vp(session: Session):
    return projects.create_project(session, "VP", "VibePod")


def ready_task(session: Session, project, title: str, **fields):
    task = ideas.create_idea(session, ADMIN, title=title, project_id=project.id, **fields)
    ideas.mark_ready(session, ADMIN, task.id)
    return task


def card_of(session: Session, task) -> object:
    return board.get_card(session, ADMIN, task.id)


def test_assigns_a_task_and_mirrors_the_holder_onto_its_card(session: Session, vp) -> None:
    task = ready_task(session, vp, "Needs an owner")
    updated = ideas.update_idea(session, ADMIN, task.id, assignee=AGENT)
    assert updated.assignee == AGENT
    assert card_of(session, task).assignee == AGENT


def test_carries_the_holder_onto_a_card_created_later(session: Session, vp) -> None:
    task = ideas.create_idea(
        session, ADMIN, title="Claimed early", project_id=vp.id, assignee=AGENT
    )
    ideas.mark_ready(session, ADMIN, task.id)
    assert card_of(session, task).assignee == AGENT


def test_releases_the_task_when_the_assignee_is_cleared(session: Session, vp) -> None:
    task = ready_task(session, vp, "Handed back")
    ideas.update_idea(session, ADMIN, task.id, assignee=AGENT)
    released = ideas.update_idea(session, ADMIN, task.id, assignee="")
    assert released.assignee is None
    assert card_of(session, task).assignee is None


def test_writes_a_claim_made_on_the_card_through_to_its_task(session: Session, vp) -> None:
    task = ready_task(session, vp, "Claimed from the board")
    board.update_card(session, ADMIN, card_of(session, task).id, assignee=AGENT)
    assert ideas.get_idea(session, ADMIN, task.id).assignee == AGENT


def test_a_card_claim_survives_a_later_task_edit(session: Session, vp) -> None:
    task = ready_task(session, vp, "Edited after the claim")
    board.update_card(session, ADMIN, card_of(session, task).id, assignee=AGENT)
    ideas.update_idea(session, ADMIN, task.id, title="Renamed by someone else")
    assert card_of(session, task).assignee == AGENT


def test_leaves_the_holder_alone_when_a_card_write_omits_it(session: Session, vp) -> None:
    task = ready_task(session, vp, "Moved, not reassigned")
    ideas.update_idea(session, ADMIN, task.id, assignee=AGENT)
    board.update_card(session, ADMIN, card_of(session, task).id, column=BoardColumn.IN_PROGRESS)
    assert card_of(session, task).assignee == AGENT
    assert ideas.get_idea(session, ADMIN, task.id).assignee == AGENT


def test_refuses_a_competing_claim_guarded_by_expected_updated_at(session: Session, vp) -> None:
    task = ready_task(session, vp, "Contended")
    read = ideas.get_idea(session, ADMIN, task.id)
    ideas.update_idea(session, ADMIN, task.id, assignee=AGENT)

    with pytest.raises(Conflict, match="changed since"):
        ideas.update_idea(
            session,
            ADMIN,
            task.id,
            assignee="Claude::Subagent202::Worktree7",
            expected_updated_at=read.updated_at,
        )
    assert ideas.get_idea(session, ADMIN, task.id).assignee == AGENT


def test_trims_surrounding_whitespace(session: Session, vp) -> None:
    task = ready_task(session, vp, "Padded")
    assert ideas.update_idea(session, ADMIN, task.id, assignee=f"  {AGENT}  ").assignee == AGENT


def test_export_and_import_keep_the_holder(session: Session, vp) -> None:
    task = ready_task(session, vp, "Held", assignee=AGENT)
    bundle = transfer.export_project(session, vp.id)
    assert bundle.ideas[0].assignee == AGENT
    assert bundle.board_cards[0].assignee == AGENT

    transfer.import_project(session, bundle, replace_existing=True)
    assert ideas.get_idea(session, ADMIN, task.id).assignee == AGENT
    assert card_of(session, task).assignee == AGENT


# --- filters -------------------------------------------------------------------------


@pytest.fixture
def holders(session: Session, vp):
    for title, assignee in (("Mine", AGENT), ("Theirs", OTHER), ("Free", None)):
        ready_task(session, vp, title, assignee=assignee)
    return vp


def task_titles(session: Session, **filters) -> list[str]:
    listed = ideas.list_ideas(session, ADMIN, filters=IdeaFilter(project="VP", **filters))
    return sorted(idea.title for idea in listed)


def card_titles(session: Session, **filters) -> list[str]:
    listed = board.list_cards(session, ADMIN, filters=CardFilter(project="VP", **filters))
    return sorted(card.title for card in listed)


def test_lists_only_the_named_holders_tasks(session: Session, holders) -> None:
    assert task_titles(session, assignee=[AGENT]) == ["Mine"]


def test_lists_tasks_nobody_holds(session: Session, holders) -> None:
    assert task_titles(session, unassigned=True) == ["Free"]


def test_widens_rather_than_narrows_when_both_are_given(session: Session, holders) -> None:
    assert task_titles(session, assignee=[AGENT], unassigned=True) == ["Free", "Mine"]


def test_returns_every_task_when_neither_is_given(session: Session, holders) -> None:
    assert len(task_titles(session)) == 3


def test_filters_board_cards_by_holder(session: Session, holders) -> None:
    assert card_titles(session, assignee=[OTHER]) == ["Theirs"]
    assert card_titles(session, unassigned=True) == ["Free"]


def test_filters_paged_lists(session: Session, holders) -> None:
    page = ideas.list_ideas_page(
        session, ADMIN, IdeaFilter(project="VP", assignee=[AGENT, OTHER], limit=10)
    )
    assert sorted(idea.title for idea in page.items) == ["Mine", "Theirs"]
    cards = board.list_cards_page(
        session, ADMIN, CardFilter(project="VP", unassigned=True, limit=10)
    )
    assert [card.title for card in cards.items] == ["Free"]


# --- REST ----------------------------------------------------------------------------


def test_rest_assigns_a_task_and_filters_by_holder(client: TestClient) -> None:
    login(client)
    created = client.post("/api/ideas", json={"title": "Claimed over REST", "assignee": AGENT})
    assert created.status_code == 201
    task_id = created.json()["item"]["id"]
    assert client.post(f"/api/ideas/{task_id}/ready").status_code == 200
    client.post("/api/ideas", json={"title": "Nobody holds me"})

    def listed(params) -> list[str]:
        return [idea["title"] for idea in client.get("/api/ideas", params=params).json()["items"]]

    assert listed({"assignee": AGENT}) == ["Claimed over REST"]
    assert listed({"unassigned": "true"}) == ["Nobody holds me"]
    assert sorted(listed([("assignee", AGENT), ("unassigned", "true")])) == [
        "Claimed over REST",
        "Nobody holds me",
    ]
    # Repeated and comma-separated holders both work.
    assert listed([("assignee", AGENT), ("assignee", OTHER)]) == ["Claimed over REST"]
    assert listed({"assignee": f"{OTHER},{AGENT}"}) == ["Claimed over REST"]

    board_state = client.get("/api/board", params={"assignee": AGENT}).json()
    assert board_state["columns"]["ready"][0]["assignee"] == AGENT

    released = client.patch(f"/api/ideas/{task_id}", json={"assignee": ""})
    assert released.status_code == 200
    assert "assignee" not in released.json()["item"]


def test_rest_claims_a_task_from_its_board_card(client: TestClient) -> None:
    login(client)
    task_id = client.post("/api/ideas", json={"title": "Claimed from the board"}).json()["item"][
        "id"
    ]
    client.post(f"/api/ideas/{task_id}/ready")
    card_id = client.get("/api/board").json()["columns"]["ready"][0]["id"]

    assert client.patch(f"/api/board/{card_id}", json={"assignee": OTHER}).status_code == 200
    assert client.get(f"/api/ideas/{task_id}").json()["item"]["assignee"] == OTHER
