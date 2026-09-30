import json
from collections.abc import Iterator
from dataclasses import replace

import pytest
from conftest import call, login, serve
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError
from github_fake import FakeGitHub
from sqlalchemy import Engine
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.config import Settings
from vibepod_board.enums import BoardColumn, RunOutcome, TaskEventKind
from vibepod_board.errors import BadRequest, Conflict, NotFound
from vibepod_board.github import GitHubClient, GitHubUnavailable, parse_pull_url
from vibepod_board.main import create_app
from vibepod_board.schemas import BoardCard
from vibepod_board.services import board, history, ideas, projects, pull_requests, runs, tokens

ADMIN = admin_access("admin")
REPO = "vibepod/board"
REMOTE = "git@github.com:vibepod/board.git"


@pytest.fixture
def fake() -> FakeGitHub:
    github = FakeGitHub()
    github.branches[REPO] = ["main"]
    return github


@pytest.fixture
def github(fake: FakeGitHub) -> GitHubClient:
    return GitHubClient("token", fake.transport)


@pytest.fixture
def project(session: Session):
    return projects.create_project(session, "APP", "App")


def pr_ready_card(
    session: Session, project, branch: str | None = "feature/pr", **fields
) -> BoardCard:
    """A task approved in Review, on `branch`, with its remote on GitHub."""
    fields.setdefault("repository_remote_url", REMOTE)
    fields.setdefault("title", "Open PRs")
    idea = ideas.create_idea(session, ADMIN, project_id=project.id, **fields)
    ideas.mark_ready(session, ADMIN, idea.id)
    card = board.get_card(session, ADMIN, idea.id)
    board.update_card(session, ADMIN, card.id, column=BoardColumn.REVIEW, branch_name=branch)
    return board.update_card(session, ADMIN, card.id, column=BoardColumn.PR_READY)


def events(session: Session, card: BoardCard) -> list[tuple[str, str]]:
    return [
        (event.kind, event.message)
        for event in history.list_task_history(session, ADMIN, card.idea_id)
    ]


# --- URL helpers --------------------------------------------------------------------


def test_parses_pull_urls() -> None:
    ref = parse_pull_url(" https://github.com/VibePod/Board/pull/42/ ")
    assert ref is not None and (ref.repository, ref.number) == (REPO, 42)
    assert ref.url == f"https://github.com/{REPO}/pull/42"
    assert parse_pull_url(f"https://github.com/{REPO}/issues/42") is None
    assert parse_pull_url(f"https://gitlab.com/{REPO}/pull/42") is None


# --- prefill ------------------------------------------------------------------------


def test_prefills_title_base_and_body_from_the_task(
    session: Session, project, github: GitHubClient
) -> None:
    card = pr_ready_card(
        session,
        project,
        summary="Open the PR from the board",
        acceptance_criteria=["Dialog is prefilled", "Branch must be pushed"],
        github_issue_url=f"https://github.com/{REPO}/issues/7",
    )
    runs.add_run_report(
        session, ADMIN, card.idea_id, RunOutcome.DONE, summary="Added the Open PR dialog"
    )

    draft = pull_requests.pull_request_draft(session, ADMIN, card.id, github)

    assert (draft.repository, draft.title, draft.base, draft.bases) == (
        REPO,
        "Open PRs",
        "main",
        ["main"],
    )
    assert not draft.draft
    assert draft.body == (
        "## Summary\nOpen the PR from the board\n\n"
        "## Acceptance Criteria\n- [ ] Dialog is prefilled\n- [ ] Branch must be pushed\n\n"
        "Closes #7\n\n"
        "## Review\n- Review approved by admin\n\n"
        "## Latest run\nAdded the Open PR dialog"
    )


def test_closes_only_issues_in_the_same_repository(
    session: Session, project, github: GitHubClient
) -> None:
    card = pr_ready_card(
        session, project, github_issue_url="https://github.com/vibepod/other/issues/7"
    )
    assert "Closes" not in pull_requests.pull_request_draft(session, ADMIN, card.id, github).body


def test_offers_the_branch_of_an_unmerged_dependency_pr_as_base(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    fake.branches[REPO] += ["feature/base", "feature/merged"]
    base = pr_ready_card(session, project, "feature/base", title="Base")
    merged = pr_ready_card(session, project, "feature/merged", title="Merged")
    pull_requests.open_pull_request(session, ADMIN, base.id, github)
    pull_requests.open_pull_request(session, ADMIN, merged.id, github)
    fake.pulls[(REPO, 2)].update(state="closed", merged_at=fake.tick())
    stacked = pr_ready_card(session, project, depends_on=[base.idea_id, merged.idea_id])

    draft = pull_requests.pull_request_draft(session, ADMIN, stacked.id, github)
    assert (draft.base, draft.bases) == ("feature/base", ["main", "feature/base"])

    fake.pulls[(REPO, 1)].update(state="closed", merged_at=fake.tick())
    draft = pull_requests.pull_request_draft(session, ADMIN, stacked.id, github)
    assert (draft.base, draft.bases) == ("main", ["main"])


def test_needs_a_github_repository(session: Session, project, github: GitHubClient) -> None:
    card = pr_ready_card(session, project, repository_remote_url="git@gitlab.com:x/y.git")
    with pytest.raises(BadRequest, match="No GitHub repository"):
        pull_requests.pull_request_draft(session, ADMIN, card.id, github)


def test_takes_the_repository_from_the_linked_issue(
    session: Session, project, github: GitHubClient
) -> None:
    card = pr_ready_card(
        session,
        project,
        repository_remote_url=None,
        github_issue_url=f"https://github.com/{REPO}/issues/3",
    )
    assert pull_requests.pull_request_draft(session, ADMIN, card.id, github).repository == REPO


# --- open ---------------------------------------------------------------------------


def test_opens_the_pr_for_the_cards_branch(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    fake.branches[REPO].append("feature/pr")
    card = pr_ready_card(session, project, summary="Why")

    opened = pull_requests.open_pull_request(
        session, ADMIN, card.id, github, title="Edited title", body="Edited body", draft=True
    )

    remote = fake.pulls[(REPO, 1)]
    assert (remote["title"], remote["body"], remote["draft"]) == (
        "Edited title",
        "Edited body",
        True,
    )
    assert (remote["head"]["ref"], remote["base"]["ref"]) == ("feature/pr", "main")
    assert opened.github_pr_url == f"https://github.com/{REPO}/pull/1"
    assert (opened.github_pr_number, opened.github_pr_repository) == (1, REPO)
    assert (opened.github_pr_state, opened.github_pr_draft, opened.github_pr_base) == (
        "open",
        True,
        "main",
    )
    assert opened.github_pr_synced_at is not None
    assert (TaskEventKind.PR_OPENED, "PR #1 opened by admin") in events(session, card)


def test_fills_left_out_fields_from_the_prefill(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    fake.branches[REPO].append("feature/pr")
    card = pr_ready_card(session, project, summary="Why")

    pull_requests.open_pull_request(session, ADMIN, card.id, github)

    remote = fake.pulls[(REPO, 1)]
    assert (remote["title"], remote["draft"], remote["base"]["ref"]) == ("Open PRs", False, "main")
    assert remote["body"].startswith("## Summary\nWhy")


def test_refuses_a_branch_that_is_not_on_github(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = pr_ready_card(session, project, "feature/unpushed")
    with pytest.raises(BadRequest, match="Push branch `feature/unpushed` to GitHub first"):
        pull_requests.open_pull_request(session, ADMIN, card.id, github)
    assert fake.pulls == {}
    assert board.get_card(session, ADMIN, card.id).github_pr_number is None


def test_links_the_pr_that_already_exists_for_the_branch(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    fake.branches[REPO].append("feature/pr")
    fake.add_pull(REPO, "feature/pr", draft=True)
    card = pr_ready_card(session, project)

    linked = pull_requests.open_pull_request(session, ADMIN, card.id, github)

    assert len(fake.pulls) == 1
    assert (linked.github_pr_number, linked.github_pr_state, linked.github_pr_draft) == (
        1,
        "open",
        True,
    )
    assert (TaskEventKind.PR_LINKED, "PR #1 linked by admin") in events(session, card)


def test_reports_other_validation_failures(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    fake.branches[REPO].append("feature/pr")
    card = pr_ready_card(session, project)
    with pytest.raises(GitHubUnavailable, match="422 Validation Failed: base is invalid"):
        pull_requests.open_pull_request(session, ADMIN, card.id, github, base="nope")


def test_only_opens_prs_for_cards_in_pr_ready_with_a_branch(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = pr_ready_card(session, project, branch=None)
    with pytest.raises(BadRequest, match="Set the card's branch"):
        pull_requests.open_pull_request(session, ADMIN, card.id, github)

    board.update_card(session, ADMIN, card.id, column=BoardColumn.REVIEW, branch_name="b")
    with pytest.raises(Conflict, match="Only cards in PR ready"):
        pull_requests.open_pull_request(session, ADMIN, card.id, github)

    board.update_card(session, ADMIN, card.id, column=BoardColumn.PR_READY)
    with pytest.raises(BadRequest, match="title is required"):
        pull_requests.open_pull_request(session, ADMIN, card.id, github, title=" ")
    assert fake.pulls == {}


def test_does_not_open_a_second_pr_while_one_is_linked(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    fake.branches[REPO].append("feature/pr")
    card = pr_ready_card(session, project)
    pull_requests.open_pull_request(session, ADMIN, card.id, github)
    with pytest.raises(Conflict, match="already has PR #1"):
        pull_requests.open_pull_request(session, ADMIN, card.id, github)


# --- link ---------------------------------------------------------------------------


def test_links_and_unlinks_a_pr_by_url(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = pr_ready_card(session, project)
    fake.add_pull("vibepod/other", "fix", base="develop", state="closed", merged=True)

    linked = pull_requests.link_pull_request(
        session, ADMIN, card.id, "https://github.com/vibepod/other/pull/1", github
    )
    assert (linked.github_pr_repository, linked.github_pr_number) == ("vibepod/other", 1)
    assert (linked.github_pr_state, linked.github_pr_base) == ("merged", "develop")

    unlinked = pull_requests.link_pull_request(session, ADMIN, card.id, " ")
    assert unlinked.github_pr_url is None
    assert unlinked.github_pr_number is None
    assert unlinked.github_pr_state is None
    assert events(session, card)[:2] == [
        (TaskEventKind.PR_UNLINKED, "PR #1 unlinked by admin"),
        (TaskEventKind.PR_LINKED, "PR #1 linked by admin"),
    ]


def test_links_without_a_token_by_identity_only(session: Session, project) -> None:
    card = pr_ready_card(session, project)
    linked = pull_requests.link_pull_request(
        session, ADMIN, card.id, f"https://github.com/{REPO}/pull/12"
    )
    assert (linked.github_pr_url, linked.github_pr_number) == (
        f"https://github.com/{REPO}/pull/12",
        12,
    )
    assert linked.github_pr_state is None


def test_rejects_invalid_and_unknown_pr_urls(
    session: Session, project, github: GitHubClient
) -> None:
    card = pr_ready_card(session, project)
    with pytest.raises(BadRequest, match="GitHub PR URL must look like"):
        pull_requests.link_pull_request(
            session, ADMIN, card.id, f"https://github.com/{REPO}/issues/1"
        )
    with pytest.raises(NotFound, match=f"Pull request not found on GitHub: {REPO}#9"):
        pull_requests.link_pull_request(
            session, ADMIN, card.id, f"https://github.com/{REPO}/pull/9", github
        )


def test_unlinking_a_card_without_a_pr_records_nothing(session: Session, project) -> None:
    card = pr_ready_card(session, project)
    before = events(session, card)
    pull_requests.link_pull_request(session, ADMIN, card.id, "")
    assert events(session, card) == before


# --- REST ---------------------------------------------------------------------------


@pytest.fixture
def github_api(db: Engine, settings: Settings, fake: FakeGitHub) -> Iterator[TestClient]:
    configured = replace(settings, github_token="token")
    app = create_app(configured, run_migrations=False, github_transport=fake.transport)
    with TestClient(app) as test_client:
        yield login(test_client)


def test_rest_prefills_opens_and_links(
    github_api: TestClient, session: Session, project, fake: FakeGitHub
) -> None:
    fake.branches[REPO].append("feature/pr")
    card = pr_ready_card(session, project)
    path = f"/api/board/{card.id}/pull-request"

    draft = github_api.get(path).json()
    assert (draft["title"], draft["base"], draft["bases"]) == ("Open PRs", "main", ["main"])

    opened = github_api.post(path, json={"title": "From REST", "draft": True})
    assert opened.status_code == 200, opened.text
    item = opened.json()["item"]
    assert (item["githubPrNumber"], item["githubPrState"], item["githubPrDraft"]) == (
        1,
        "open",
        True,
    )
    assert fake.pulls[(REPO, 1)]["title"] == "From REST"

    unlinked = github_api.post(f"{path}/link", json={"url": ""})
    assert unlinked.json()["item"].get("githubPrUrl") is None
    relinked = github_api.post(f"{path}/link", json={"url": f"https://github.com/{REPO}/pull/1"})
    assert relinked.json()["item"]["githubPrState"] == "open"


def test_rest_refuses_unpushed_branches(github_api: TestClient, session: Session, project) -> None:
    card = pr_ready_card(session, project)
    response = github_api.post(f"/api/board/{card.id}/pull-request", json={})
    assert response.status_code == 400
    assert response.json() == {"error": "Push branch `feature/pr` to GitHub first"}


def test_rest_needs_a_token_to_open_but_not_to_link(
    client: TestClient, session: Session, project
) -> None:
    agent = login(client)
    card = pr_ready_card(session, project)
    path = f"/api/board/{card.id}/pull-request"

    assert agent.get(path).status_code == 503
    response = agent.post(path, json={})
    assert response.status_code == 503
    assert response.json() == {"error": "GitHub sync is disabled: set GITHUB_TOKEN"}

    linked = agent.post(f"{path}/link", json={"url": f"https://github.com/{REPO}/pull/4"})
    assert linked.status_code == 200
    assert linked.json()["item"]["githubPrNumber"] == 4


# --- MCP ----------------------------------------------------------------------------


@pytest.fixture
def github_server_url(db: Engine, settings: Settings, fake: FakeGitHub) -> Iterator[str]:
    configured = replace(settings, github_token="token")
    app = create_app(configured, run_migrations=False, github_transport=fake.transport)
    with serve(app) as url:
        yield url


async def test_mcp_opens_and_links_pull_requests(
    github_server_url: str, session: Session, project, fake: FakeGitHub
) -> None:
    fake.branches[REPO].append("feature/pr")
    card = pr_ready_card(session, project)
    token = tokens.create_token(session, "Agent", [project.id]).token
    other = pr_ready_card(session, project, title="Other")
    board.update_card(session, ADMIN, other.id, column=BoardColumn.DONE)

    with pytest.raises(ToolError, match="Only cards in PR ready"):
        await call(github_server_url, token, "open_pull_request", id="APP-2")

    opened = await call(github_server_url, token, "open_pull_request", id=card.id, draft=True)
    assert (opened["item"]["githubPrNumber"], opened["item"]["githubPrDraft"]) == (1, True)

    async def link(url: str) -> dict:
        # `call` takes the server URL as `url`, so the tool's own `url` goes around it.
        async with Client(github_server_url, auth=token) as client:
            result = await client.call_tool("link_pull_request", {"id": "APP-1", "url": url})
            return json.loads(result.content[0].text)["item"]

    assert (await link("")).get("githubPrNumber") is None
    assert (await link(f"https://github.com/{REPO}/pull/1"))["githubPrState"] == "open"
