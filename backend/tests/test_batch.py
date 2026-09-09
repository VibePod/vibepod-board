"""Batch writes: up to fifty task or card writes in one all-or-nothing transaction."""

import pytest
from conftest import login
from fastapi.testclient import TestClient
from sqlmodel import Session

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import BoardColumn
from vibepod_board.errors import BadRequest, Conflict, NotFound
from vibepod_board.services import activity, board, ideas, projects

ADMIN = admin_access("admin")


@pytest.fixture
def three(session: Session):
    vp = projects.create_project(session, "VP", "VibePod")
    tasks = [
        ideas.create_idea(session, ADMIN, title=title, project_id=vp.id)
        for title in ("One", "Two", "Three")
    ]
    return vp, tasks


def labels_of(session: Session, reference: str) -> list[str]:
    return ideas.get_idea(session, ADMIN, reference).labels


def test_applies_every_item_and_returns_them_in_input_order(session: Session, three) -> None:
    _, tasks = three
    updated = ideas.update_ideas(
        session,
        ADMIN,
        [
            {"id": "VP-1", "labels": ["api"]},
            {"id": "VP-2", "labels": ["ui"]},
            {"id": "VP-3", "labels": ["docs"]},
        ],
    )
    assert [idea.id for idea in updated] == [task.id for task in tasks]
    assert [idea.labels for idea in updated] == [["api"], ["ui"], ["docs"]]


def test_rolls_the_whole_batch_back_when_one_item_fails(session: Session, three) -> None:
    with pytest.raises(NotFound, match=r"Batch item 2 \(VP-999\) failed: Task not found: VP-999"):
        ideas.update_ideas(
            session,
            ADMIN,
            [
                {"id": "VP-1", "labels": ["applied"]},
                {"id": "VP-999", "labels": ["missing"]},
                {"id": "VP-3", "labels": ["applied"]},
            ],
        )
    assert labels_of(session, "VP-1") == []


def test_rolls_back_on_a_stale_item_and_names_it(session: Session, three) -> None:
    _, tasks = three
    ideas.update_idea(session, ADMIN, "VP-2", summary="Moved on")
    with pytest.raises(Conflict, match=r"Batch item 2 \(VP-2\) failed: Task VP-2 changed since"):
        ideas.update_ideas(
            session,
            ADMIN,
            [
                {"id": "VP-1", "labels": ["applied"]},
                {"id": "VP-2", "labels": ["stale"], "expected_updated_at": tasks[1].updated_at},
            ],
        )
    assert labels_of(session, "VP-1") == []


def test_aborts_when_one_item_is_out_of_scope(session: Session, three) -> None:
    vp, _ = three
    other = projects.create_project(session, "OPS", "Operations")
    ideas.create_idea(session, ADMIN, title="Foreign", project_id=other.id)
    scoped = token_access("token-1", [vp.id])

    with pytest.raises(NotFound, match=r"Batch item 2 \(OPS-1\) failed: Task not found: OPS-1"):
        ideas.update_ideas(
            session,
            scoped,
            [{"id": "VP-1", "labels": ["applied"]}, {"id": "OPS-1", "labels": ["forbidden"]}],
        )
    assert ideas.get_idea(session, scoped, "VP-1").labels == []


def test_writes_one_activity_row_for_the_whole_batch(session: Session, three) -> None:
    before = len(activity.list_activity(session, ADMIN))
    ideas.update_ideas(
        session,
        ADMIN,
        [{"id": f"VP-{n}", "labels": ["x"]} for n in (1, 2, 3)],
    )
    after = activity.list_activity(session, ADMIN)
    assert len(after) == before + 1
    assert (after[0].type, after[0].message) == ("idea.updated", "Updated 3 tasks")


def test_refuses_more_than_fifty_items(session: Session, three) -> None:
    items = [{"id": "VP-1", "labels": ["api"]}] * 51
    with pytest.raises(BadRequest, match="Batch is limited to 50 items; received 51"):
        ideas.update_ideas(session, ADMIN, items)
    with pytest.raises(BadRequest, match="Batch is limited to 50 items; received 51"):
        board.update_cards(session, ADMIN, items)


def test_an_empty_batch_writes_nothing(session: Session, three) -> None:
    before = len(activity.list_activity(session, ADMIN))
    assert ideas.update_ideas(session, ADMIN, []) == []
    assert board.update_cards(session, ADMIN, []) == []
    assert len(activity.list_activity(session, ADMIN)) == before


def test_moves_several_cards_in_one_batch(session: Session, three) -> None:
    vp, tasks = three
    for task in tasks:
        ideas.mark_ready(session, ADMIN, task.id)

    moved = board.update_cards(
        session,
        ADMIN,
        [
            {"id": "VP-1", "column": BoardColumn.PLANNED},
            {"id": "VP-2", "column": BoardColumn.IN_PROGRESS, "branch_name": "vp-2"},
        ],
    )
    assert [card.column for card in moved] == ["planned", "in_progress"]
    assert moved[1].branch_name == "vp-2"
    assert len(board.board_columns(session, ADMIN, vp.id).ready) == 1
    latest = activity.list_activity(session, ADMIN)[0]
    assert (latest.type, latest.message) == ("board.moved", "Moved 2 of 2 cards")


def test_a_failing_card_batch_rolls_back(session: Session, three) -> None:
    vp, tasks = three
    for task in tasks:
        ideas.mark_ready(session, ADMIN, task.id)
    with pytest.raises(NotFound, match=r"Batch item 2 \(VP-999\) failed"):
        board.update_cards(
            session,
            ADMIN,
            [{"id": "VP-1", "column": BoardColumn.DONE}, {"id": "VP-999", "column": "done"}],
        )
    assert len(board.board_columns(session, ADMIN, vp.id).ready) == 3


# --- REST ----------------------------------------------------------------------------


def test_rest_applies_a_batch_of_task_updates_atomically(client: TestClient) -> None:
    login(client)
    client.post("/api/projects", json={"key": "VP", "title": "VibePod"})
    for title in ("One", "Two"):
        client.post("/api/ideas", json={"projectId": "VP", "title": title})

    applied = client.post(
        "/api/ideas/batch",
        json={"items": [{"id": "VP-1", "labels": ["api"]}, {"id": "VP-2", "labels": ["ui"]}]},
    )
    assert applied.status_code == 200
    assert [item["key"] for item in applied.json()["items"]] == ["VP-1", "VP-2"]
    assert applied.json()["items"][0]["labels"] == ["api"]

    rejected = client.post(
        "/api/ideas/batch",
        json={
            "items": [
                {"id": "VP-1", "labels": ["rolled-back"]},
                {"id": "VP-999", "labels": ["missing"]},
            ]
        },
    )
    assert rejected.status_code == 404
    assert "Batch item 2 (VP-999)" in rejected.json()["error"]
    assert client.get("/api/ideas/VP-1").json()["item"]["labels"] == ["api"]

    refs = client.post(
        "/api/ideas/batch", params={"view": "ref"}, json={"items": [{"id": "VP-2", "summary": "s"}]}
    ).json()["items"]
    assert set(refs[0]) == {"id", "key", "updatedAt"}

    too_many = client.post("/api/ideas/batch", json={"items": [{"id": "VP-1"}] * 51})
    assert too_many.status_code == 400


def test_rest_moves_cards_in_a_batch(client: TestClient) -> None:
    login(client)
    client.post("/api/projects", json={"key": "VP", "title": "VibePod"})
    for title in ("One", "Two"):
        task_id = client.post("/api/ideas", json={"projectId": "VP", "title": title}).json()[
            "item"
        ]["id"]
        client.post(f"/api/ideas/{task_id}/ready")

    moved = client.post(
        "/api/board/batch",
        json={"items": [{"id": "VP-1", "column": "review"}, {"id": "VP-2", "column": "done"}]},
    )
    assert moved.status_code == 200
    assert [item["column"] for item in moved.json()["items"]] == ["review", "done"]
    assert client.get("/api/board/VP-2").json()["item"]["column"] == "done"
