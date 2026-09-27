from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta

import pytest
from conftest import login
from fastapi.testclient import TestClient
from github_fake import T0, FakeGitHub
from sqlalchemy import Engine
from sqlmodel import Session

from vibepod_board.access import admin_access, token_access
from vibepod_board.config import Settings
from vibepod_board.errors import BadRequest, Conflict, Forbidden, NotFound
from vibepod_board.github import GitHubClient, parse_issue_url, repository_from_remote
from vibepod_board.main import create_app
from vibepod_board.services import board, github_sync, ideas, projects, transfer
from vibepod_board.services.github_link import INVALID_URL

ADMIN = admin_access("admin")
REPO = "vibepod/board"


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def github(fake: FakeGitHub) -> GitHubClient:
    return GitHubClient("token", fake.transport)


@pytest.fixture
def project(session: Session):
    return projects.create_project(session, "APP", "App")


def task(session: Session, project, **fields):
    return ideas.create_idea(session, ADMIN, project_id=project.id, **fields)


# --- URL helpers --------------------------------------------------------------------


def test_parses_issue_urls_and_github_remotes() -> None:
    ref = parse_issue_url("https://github.com/vibepod/board/issues/42")
    assert ref is not None and (ref.repository, ref.number) == (REPO, 42)
    assert parse_issue_url("https://github.com/vibepod/board/pull/42") is None
    assert parse_issue_url("https://gitlab.com/vibepod/board/issues/42") is None

    assert repository_from_remote("git@github.com:vibepod/board.git") == REPO
    assert repository_from_remote("https://github.com/vibepod/board") == REPO
    assert repository_from_remote("git@gitlab.com:vibepod/board.git") is None
    assert repository_from_remote(None) is None


# --- push ---------------------------------------------------------------------------


def test_push_creates_the_issue_and_links_the_task(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    created = task(
        session,
        project,
        title="Ship sync",
        summary="Two-way GitHub sync",
        acceptance_criteria=["Push works"],
        labels=["sync"],
        repository_remote_url="git@github.com:vibepod/board.git",
    )

    pushed = github_sync.push(session, ADMIN, created.id, github, default_repository=None)

    remote = fake.issues[(REPO, 1)]
    assert remote["title"] == "Ship sync"
    assert (
        remote["body"] == "## Summary\nTwo-way GitHub sync\n\n## Acceptance Criteria\n- Push works"
    )
    assert [label["name"] for label in remote["labels"]] == ["sync"]
    assert (pushed.github_repository, pushed.github_issue_number) == (REPO, 1)
    assert pushed.github_issue_url == "https://github.com/vibepod/board/issues/1"
    assert pushed.github_issue_state == "open"
    assert pushed.github_synced_at is not None


def test_push_falls_back_to_the_default_repository(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    created = task(session, project, title="No remote")

    with pytest.raises(BadRequest, match="No GitHub repository for this task"):
        github_sync.push(session, ADMIN, created.id, github, default_repository=None)

    pushed = github_sync.push(session, ADMIN, created.id, github, default_repository="org/app")
    assert pushed.github_repository == "org/app"


def test_push_updates_a_linked_issue_but_refuses_to_overwrite_remote_changes(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    created = task(session, project, title="Original")
    github_sync.push(session, ADMIN, created.id, github, default_repository=REPO)

    ideas.update_idea(session, ADMIN, created.id, title="Renamed locally")
    updated = github_sync.push(session, ADMIN, created.id, github, default_repository=REPO)
    assert fake.issues[(REPO, 1)]["title"] == "Renamed locally"
    assert updated.github_issue_updated_at is not None

    fake.edit(REPO, 1, title="Renamed on GitHub")
    with pytest.raises(Conflict, match="pull first"):
        github_sync.push(session, ADMIN, created.id, github, default_repository=REPO)
    assert fake.issues[(REPO, 1)]["title"] == "Renamed on GitHub"


def test_push_never_changes_the_remote_state(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    created = task(session, project, title="Closed remotely")
    github_sync.push(session, ADMIN, created.id, github, default_repository=REPO)
    fake.edit(REPO, 1, state="closed")
    github_sync.pull(session, ADMIN, created.id, github)

    github_sync.push(session, ADMIN, created.id, github, default_repository=REPO)

    assert fake.issues[(REPO, 1)]["state"] == "closed"


# --- pull ---------------------------------------------------------------------------


def test_pull_refreshes_title_labels_and_state_but_keeps_details_and_status(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    fake.add_issue(REPO, 7, "Remote title", body="Remote body", labels=("bug",))
    created = task(session, project, title="Local", details="Local refinement")
    ideas.update_idea(
        session, ADMIN, created.id, github_issue_url=f"https://github.com/{REPO}/issues/7"
    )
    ideas.mark_ready(session, ADMIN, created.id)
    fake.edit(REPO, 7, state="closed")

    pulled = github_sync.pull(session, ADMIN, created.id, github)

    assert pulled.title == "Remote title"
    assert pulled.labels == ["bug"]
    assert pulled.github_issue_state == "closed"
    assert pulled.details == "Local refinement"
    assert pulled.status == "ready"
    card = board.board_columns(session, ADMIN, project.id).ready[0]
    assert (card.title, card.github_issue_number) == ("Remote title", 7)


def test_pull_fills_empty_details_from_the_issue_body(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    fake.add_issue(REPO, 3, "Remote", body="Steps to reproduce")
    created = task(session, project, title="Local")
    ideas.update_idea(
        session, ADMIN, created.id, github_issue_url=f"https://github.com/{REPO}/issues/3"
    )

    assert github_sync.pull(session, ADMIN, created.id, github).details == "Steps to reproduce"


def test_pull_requires_a_link_and_an_existing_issue(
    session: Session, project, github: GitHubClient
) -> None:
    created = task(session, project, title="Unlinked")
    with pytest.raises(Conflict, match="not linked"):
        github_sync.pull(session, ADMIN, created.id, github)

    ideas.update_idea(
        session, ADMIN, created.id, github_issue_url=f"https://github.com/{REPO}/issues/99"
    )
    with pytest.raises(NotFound, match=f"Issue not found on GitHub: {REPO}#99"):
        github_sync.pull(session, ADMIN, created.id, github)


def test_push_and_pull_stay_inside_the_token_scope(
    session: Session, project, github: GitHubClient
) -> None:
    other = projects.create_project(session, "OTH", "Other")
    theirs = task(session, other, title="Off limits")
    scoped = token_access("token-1", [project.id])

    with pytest.raises(Forbidden):
        github_sync.push(session, scoped, theirs.id, github, default_repository=REPO)
    with pytest.raises(Forbidden):
        github_sync.pull(session, scoped, theirs.id, github)


# --- manual link --------------------------------------------------------------------


def test_links_unlinks_and_guards_duplicate_links(session: Session, project) -> None:
    first = task(session, project, title="First")
    second = task(session, project, title="Second")
    url = f"https://github.com/{REPO}/issues/5"

    linked = ideas.update_idea(session, ADMIN, first.id, github_issue_url=url)
    assert (linked.github_repository, linked.github_issue_number, linked.github_issue_url) == (
        REPO,
        5,
        url,
    )

    with pytest.raises(Conflict, match="already linked to task APP-1"):
        ideas.update_idea(session, ADMIN, second.id, github_issue_url=url)
    with pytest.raises(BadRequest, match=INVALID_URL):
        ideas.update_idea(session, ADMIN, second.id, github_issue_url="https://example.com/5")

    with pytest.raises(Conflict, match="already linked"):
        task(session, project, title="Third", github_issue_url=url)
    fresh = task(
        session, project, title="Fourth", github_issue_url=f"https://github.com/{REPO}/issues/6"
    )
    assert fresh.github_issue_number == 6

    unlinked = ideas.update_idea(session, ADMIN, first.id, github_issue_url="")
    assert unlinked.github_issue_url is None
    assert unlinked.github_repository is None


# --- upsert (agent import) ----------------------------------------------------------


def upsert(session: Session, access=ADMIN, minutes: int = 0, **changes):
    fields = {
        "repository": REPO,
        "number": 12,
        "url": f"https://github.com/{REPO}/issues/12",
        "title": "Imported",
        "state": "open",
        "remote_updated_at": T0 + timedelta(minutes=minutes),
        "body": "Issue body",
        "labels": ["triage"],
    } | changes
    return github_sync.upsert_issue(session, access, **fields)


def test_upsert_creates_then_refreshes_then_skips_unchanged(session: Session, project) -> None:
    created = upsert(session, project_id=project.id)
    assert (created.created, created.unchanged) == (True, False)
    assert created.item.title == "Imported"
    assert created.item.details == "Issue body"
    assert created.item.status == "refining"
    assert created.item.task_number == 1

    ideas.update_idea(session, ADMIN, created.item.id, details="Refined locally")
    refreshed = upsert(
        session, project_id=project.id, minutes=5, title="Renamed", state="closed", body="New"
    )
    assert (refreshed.created, refreshed.unchanged) == (False, False)
    assert refreshed.item.id == created.item.id
    assert refreshed.item.title == "Renamed"
    assert refreshed.item.github_issue_state == "closed"
    assert refreshed.item.details == "Refined locally"

    same = upsert(session, project_id=project.id, minutes=5, title="Ignored")
    assert (same.created, same.unchanged) == (False, True)
    assert same.item.title == "Renamed"


def test_upsert_defaults_to_the_token_project_and_respects_scope(session: Session, project) -> None:
    other = projects.create_project(session, "OTH", "Other")
    scoped = token_access("token-1", [project.id])

    assert upsert(session, scoped).item.project_id == project.id
    with pytest.raises(Forbidden):
        upsert(session, scoped, project_id=other.id)


def test_export_and_import_keep_the_github_link(session: Session, engine: Engine, project) -> None:
    upsert(session, project_id=project.id)
    bundle = transfer.export_project(session, project.id)
    exported = bundle.model_dump(mode="json")["ideas"][0]
    assert exported["githubRepository"] == REPO
    assert exported["githubIssueState"] == "open"
    assert exported["githubIssueUpdatedAt"] == "2026-09-01T12:00:00.000Z"

    result = transfer.import_project(session, bundle, replace_existing=True)
    assert result.replaced
    [reimported] = ideas.list_ideas(session, ADMIN, project.id)
    assert (reimported.github_repository, reimported.github_issue_number) == (REPO, 12)


# --- REST ---------------------------------------------------------------------------


@pytest.fixture
def github_api(db: Engine, settings: Settings, fake: FakeGitHub) -> Iterator[TestClient]:
    configured = replace(settings, github_token="token", github_repository=REPO)
    app = create_app(configured, run_migrations=False, github_transport=fake.transport)
    with TestClient(app) as test_client:
        yield login(test_client)


def test_rest_push_pull_and_status(github_api: TestClient, fake: FakeGitHub) -> None:
    assert github_api.get("/api/github").json() == {"enabled": True, "defaultRepository": REPO}
    created = github_api.post("/api/ideas", json={"title": "REST sync"}).json()["item"]

    pushed = github_api.post(f"/api/ideas/{created['id']}/github/push")
    assert pushed.status_code == 200
    assert pushed.json()["item"]["githubIssueUrl"] == f"https://github.com/{REPO}/issues/1"

    fake.edit(REPO, 1, title="Edited on GitHub")
    conflict = github_api.post(f"/api/ideas/{created['id']}/github/push")
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "Issue changed on GitHub since the last sync — pull first"}

    pulled = github_api.post(f"/api/ideas/{created['id']}/github/pull")
    assert pulled.json()["item"]["title"] == "Edited on GitHub"

    fake.status_override = 401
    rejected = github_api.post(f"/api/ideas/{created['id']}/github/pull")
    assert rejected.status_code == 502
    assert rejected.json() == {"error": "GitHub rejected the token"}


def test_rest_links_through_patch(github_api: TestClient) -> None:
    created = github_api.post("/api/ideas", json={"title": "Link me"}).json()["item"]
    url = f"https://github.com/{REPO}/issues/8"

    linked = github_api.patch(f"/api/ideas/{created['id']}", json={"githubIssueUrl": url})
    assert linked.json()["item"]["githubIssueNumber"] == 8
    invalid = github_api.patch(f"/api/ideas/{created['id']}", json={"githubIssueUrl": "nope"})
    assert invalid.status_code == 400


def test_rest_reports_disabled_sync(client: TestClient) -> None:
    agent = login(client)
    assert agent.get("/api/github").json() == {"enabled": False}
    created = agent.post("/api/ideas", json={"title": "No token"}).json()["item"]
    response = agent.post(f"/api/ideas/{created['id']}/github/push")
    assert response.status_code == 503
    assert response.json() == {"error": "GitHub sync is disabled: set GITHUB_TOKEN"}
