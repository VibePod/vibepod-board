"""List filters (status, column, kind, updatedSince) and keyset paging over (updatedAt, id)."""

import time
from datetime import UTC, datetime, timedelta

import pytest
from conftest import login
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from vibepod_board.access import admin_access, token_access
from vibepod_board.enums import BoardColumn, DocumentKind, IdeaStatus
from vibepod_board.errors import BadRequest, Forbidden, NotFound
from vibepod_board.schemas import format_timestamp
from vibepod_board.services import board, documents, ideas, projects
from vibepod_board.services.board import CardFilter
from vibepod_board.services.documents import DocumentFilter
from vibepod_board.services.ideas import IdeaFilter

ADMIN = admin_access("admin")


@pytest.fixture
def seeded(session: Session):
    vp = projects.create_project(session, "VP", "VibePod")
    refining = ideas.create_idea(session, ADMIN, title="Being refined", project_id=vp.id)
    # create_idea always inserts status "idea"; writing details escalates to refining.
    ideas.update_idea(session, ADMIN, refining.id, details="Has detail")
    ready = ideas.create_idea(session, ADMIN, title="Ready to go", project_id=vp.id)
    ideas.mark_ready(session, ADMIN, ready.id)
    board.update_card(session, ADMIN, ready.id, column=BoardColumn.IN_PROGRESS)
    return vp, refining, ready


def titles(items) -> list[str]:
    return [item.title for item in items]


def test_filters_tasks_by_status(session: Session, seeded) -> None:
    vp, _, _ = seeded

    def listed(*status: IdeaStatus) -> list:
        return ideas.list_ideas(session, ADMIN, filters=IdeaFilter(project=vp.id, status=status))

    assert titles(listed(IdeaStatus.REFINING)) == ["Being refined"]
    assert titles(listed(IdeaStatus.READY)) == ["Ready to go"]
    assert len(listed(IdeaStatus.REFINING, IdeaStatus.READY)) == 2


def test_filters_cards_by_column(session: Session, seeded) -> None:
    vp, _, _ = seeded
    columns = board.board_columns(
        session, ADMIN, filters=CardFilter(project=vp.id, column=[BoardColumn.IN_PROGRESS])
    )
    assert len(columns.in_progress) == 1
    assert columns.ready == []


def test_filters_documents_by_kind(session: Session, seeded) -> None:
    vp, _, _ = seeded
    documents.create_document(session, ADMIN, title="Plan", project_id=vp.id)
    documents.create_document(
        session, ADMIN, title="Notes", project_id=vp.id, kind=DocumentKind.NOTES
    )
    listed = documents.list_documents(
        session, ADMIN, filters=DocumentFilter(project="VP", kind=[DocumentKind.NOTES])
    )
    assert titles(listed) == ["Notes"]


def test_returns_only_records_changed_after_updated_since(session: Session, seeded) -> None:
    vp, refining, _ = seeded
    boundary = datetime.now(UTC) + timedelta(milliseconds=5)
    time.sleep(0.01)
    ideas.update_idea(session, ADMIN, refining.id, summary="Touched")

    for since in (boundary, format_timestamp(boundary)):
        changed = ideas.list_ideas(
            session, ADMIN, filters=IdeaFilter(project=vp.id, updated_since=since)
        )
        assert [idea.id for idea in changed] == [refining.id]


def test_refuses_a_malformed_updated_since(session: Session, seeded) -> None:
    with pytest.raises(BadRequest, match="updatedSince must be an ISO timestamp"):
        ideas.list_ideas(session, ADMIN, filters=IdeaFilter(updated_since="yesterday"))


def test_still_accepts_a_bare_project_reference(session: Session, seeded) -> None:
    assert len(ideas.list_ideas(session, ADMIN, "VP")) == 2
    assert len(ideas.list_ideas(session, ADMIN)) == 2


def test_returns_nothing_for_a_token_with_no_projects(session: Session, seeded) -> None:
    empty = token_access("token-1", [])
    assert ideas.list_ideas(session, empty) == []
    assert board.list_cards(session, empty) == []
    assert documents.list_documents(session, empty) == []


def test_scopes_an_unfiltered_list_to_the_token_projects(session: Session, seeded) -> None:
    vp, _, _ = seeded
    other = projects.create_project(session, "OPS", "Operations")
    ideas.create_idea(session, ADMIN, title="Hidden", project_id=other.id)
    scoped = token_access("token-1", [vp.id])

    visible = ideas.list_ideas(session, scoped)
    assert len(visible) == 2
    assert {idea.project_id for idea in visible} == {vp.id}
    # A foreign project is refused whether named by id or key; an unknown one is missing.
    with pytest.raises(Forbidden):
        ideas.list_ideas(session, scoped, "OPS")
    with pytest.raises(NotFound, match="Project not found: Nope"):
        ideas.list_ideas(session, scoped, "Nope")


# --- keyset paging -------------------------------------------------------------------


def test_pages_through_a_tie_in_updated_at_without_repeating_or_skipping(
    session: Session,
) -> None:
    vp = projects.create_project(session, "VP", "VibePod")
    for title in "ABCDE":
        ideas.create_idea(session, ADMIN, title=title, project_id=vp.id)
    # One write stamps several rows with one timestamp, so ties are routine.
    session.execute(text("update ideas set updated_at = '2026-09-03T06:00:00.000Z'"))
    session.commit()

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = ideas.list_ideas_page(
            session, ADMIN, IdeaFilter(project="VP", limit=2, cursor=cursor)
        )
        seen += [idea.id for idea in page.items]
        pages += 1
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    assert pages == 3
    assert len(seen) == len(set(seen)) == 5


def test_returns_everything_when_no_limit_is_given(session: Session) -> None:
    vp = projects.create_project(session, "VP", "VibePod")
    for title in "ABC":
        ideas.create_idea(session, ADMIN, title=title, project_id=vp.id)
    page = ideas.list_ideas_page(session, ADMIN, IdeaFilter(project="VP"))
    assert len(page.items) == 3
    assert page.next_cursor is None


def test_pages_board_cards_too(session: Session) -> None:
    vp = projects.create_project(session, "VP", "VibePod")
    for title in "ABC":
        task = ideas.create_idea(session, ADMIN, title=title, project_id=vp.id)
        ideas.mark_ready(session, ADMIN, task.id)

    first = board.list_cards_page(session, ADMIN, CardFilter(project="VP", limit=2))
    second = board.list_cards_page(
        session, ADMIN, CardFilter(project="VP", limit=2, cursor=first.next_cursor)
    )
    assert len(first.items) == 2
    assert len(second.items) == 1
    assert second.next_cursor is None
    assert {c.id for c in first.items}.isdisjoint(c.id for c in second.items)


def test_refuses_a_malformed_cursor(session: Session) -> None:
    with pytest.raises(BadRequest, match="Invalid cursor"):
        ideas.list_ideas_page(session, ADMIN, IdeaFilter(limit=2, cursor="not-a-cursor"))


# --- REST ----------------------------------------------------------------------------


def test_rest_lists_filter_and_page(client: TestClient) -> None:
    login(client)
    project = client.post("/api/projects", json={"key": "VP", "title": "VibePod"}).json()["item"]
    for title in ("A", "B", "C"):
        client.post("/api/ideas", json={"projectId": project["id"], "title": title})

    first = client.get("/api/ideas", params={"projectId": "VP", "limit": 2}).json()
    assert len(first["items"]) == 2
    assert first["items"][0]["key"].startswith("VP-")
    second = client.get(
        "/api/ideas", params={"projectId": "VP", "limit": 2, "cursor": first["nextCursor"]}
    ).json()
    assert len(second["items"]) == 1
    assert "nextCursor" not in second

    unlimited = client.get("/api/ideas").json()
    assert len(unlimited["items"]) == 3
    assert "nextCursor" not in unlimited

    assert client.get("/api/ideas", params={"status": "idea,ready"}).status_code == 200
    bad = client.get("/api/ideas", params={"status": "bogus"})
    assert bad.status_code == 400
    assert "Invalid status: bogus" in bad.json()["error"]
    assert client.get("/api/board", params={"column": "nowhere"}).status_code == 400
    assert client.get("/api/ideas", params={"projectId": "Nope"}).status_code == 404
    assert client.get("/api/ideas", params={"updatedSince": "yesterday"}).status_code == 400

    compact = client.get("/api/ideas", params={"view": "compact"}).json()["items"][0]
    assert "details" not in compact
    assert compact["detailsLength"] == 0
    docs = client.get("/api/documents", params={"kind": "notes", "view": "compact"})
    assert docs.status_code == 200
    assert docs.json() == {"items": []}
