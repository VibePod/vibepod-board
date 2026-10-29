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
from vibepod_board.github import (
    GitHubClient,
    GitHubRateLimited,
    GitHubUnavailable,
    parse_pull_url,
)
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


def test_prefills_title_base_and_a_short_body_from_the_task(
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
    # Neither the criteria, the approvals nor the latest run: only the summary.
    assert draft.body == "Open the PR from the board\n\nCloses #7"


@pytest.mark.parametrize("linked", [False, True])
def test_empty_summary_does_not_prefill_the_full_spec(
    session: Session, project, github: GitHubClient, linked: bool
) -> None:
    card = pr_ready_card(
        session,
        project,
        details="A long specification that should not become a PR description.",
        github_issue_url=f"https://github.com/{REPO}/issues/7" if linked else None,
    )
    draft = pull_requests.pull_request_draft(session, ADMIN, card.id, github)
    assert draft.body == ("Closes #7" if linked else "")


def test_prefills_from_the_suggested_pull_request(
    session: Session, project, github: GitHubClient
) -> None:
    card = pr_ready_card(
        session,
        project,
        summary="Open the PR from the board",
        github_issue_url=f"https://github.com/{REPO}/issues/7",
    )
    board.update_card(
        session,
        ADMIN,
        card.id,
        suggested_pr_title="Add the Open PR dialog",
        suggested_pr_body="Adds the dialog.\n\nTested by hand.",
    )

    draft = pull_requests.pull_request_draft(session, ADMIN, card.id, github)

    assert draft.title == "Add the Open PR dialog"
    assert draft.body == "Adds the dialog.\n\nTested by hand.\n\nCloses #7"


@pytest.mark.parametrize(
    "closing",
    [
        "Closes #7",
        "fixes #7",
        "Resolved: vibepod/board#7",
        f"Closes https://github.com/{REPO}/issues/7",
    ],
)
def test_keeps_a_suggestion_that_already_closes_the_issue(
    session: Session, project, github: GitHubClient, closing: str
) -> None:
    card = pr_ready_card(session, project, github_issue_url=f"https://github.com/{REPO}/issues/7")
    body = f"Adds the dialog.\n\n{closing}"
    board.update_card(session, ADMIN, card.id, suggested_pr_body=body)

    assert pull_requests.pull_request_draft(session, ADMIN, card.id, github).body == body


def test_closes_the_issue_when_the_suggestion_only_mentions_it(
    session: Session, project, github: GitHubClient
) -> None:
    card = pr_ready_card(session, project, github_issue_url=f"https://github.com/{REPO}/issues/7")
    board.update_card(
        session, ADMIN, card.id, suggested_pr_body="Follows #7, and closes #70 and other#7."
    )

    body = pull_requests.pull_request_draft(session, ADMIN, card.id, github).body
    assert body.endswith("\n\nCloses #7")


@pytest.mark.parametrize(
    "example",
    [
        "Write `Closes #7` to close it.",
        "```\nCloses #7\n```",
        "~~~~md\nCloses #7\n~~~\n~~~~",
        "> Closes #7",
        "<!-- Closes #7 -->",
        "<!--\nCloses #7\n-->",
    ],
)
def test_closes_the_issue_when_the_suggestion_only_shows_an_example(
    session: Session, project, github: GitHubClient, example: str
) -> None:
    card = pr_ready_card(session, project, github_issue_url=f"https://github.com/{REPO}/issues/7")
    board.update_card(session, ADMIN, card.id, suggested_pr_body=f"Adds the dialog.\n\n{example}")

    body = pull_requests.pull_request_draft(session, ADMIN, card.id, github).body
    assert body.endswith("\n\nCloses #7")


def test_keeps_a_suggestion_that_closes_the_issue_after_an_example(
    session: Session, project, github: GitHubClient
) -> None:
    card = pr_ready_card(session, project, github_issue_url=f"https://github.com/{REPO}/issues/7")
    body = "Adds the dialog.\n\n```\nexample\n```\n\n`code` and fixes #7"
    board.update_card(session, ADMIN, card.id, suggested_pr_body=body)

    assert pull_requests.pull_request_draft(session, ADMIN, card.id, github).body == body


def test_a_suggested_title_alone_keeps_the_generated_body(
    session: Session, project, github: GitHubClient
) -> None:
    card = pr_ready_card(session, project, summary="Why")
    board.update_card(session, ADMIN, card.id, suggested_pr_title="Add it")

    draft = pull_requests.pull_request_draft(session, ADMIN, card.id, github)
    assert (draft.title, draft.body) == ("Add it", "Why")


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


def test_does_not_offer_a_fork_branch_as_base(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    fake.branches[REPO] += ["feature/base"]
    base = pr_ready_card(session, project, "feature/base", title="Base")
    fork = fake.add_pull(REPO, "feature/forked", head_repository="someone/fork")
    pull_requests.link_pull_request(session, ADMIN, base.id, fork["html_url"], github)
    stacked = pr_ready_card(session, project, depends_on=[base.idea_id])

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
    assert remote["body"] == "Why"


def test_opens_the_pr_with_the_suggestion_for_left_out_fields(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    fake.branches[REPO].append("feature/pr")
    card = pr_ready_card(session, project, summary="Why")
    board.update_card(
        session, ADMIN, card.id, suggested_pr_title="Add it", suggested_pr_body="Adds it."
    )

    pull_requests.open_pull_request(session, ADMIN, card.id, github, title="Mine")

    remote = fake.pulls[(REPO, 1)]
    assert (remote["title"], remote["body"]) == ("Mine", "Adds it.")


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
    fake.add_pull("vibepod/other", "feature/pr", base="develop", draft=True)

    linked = pull_requests.link_pull_request(
        session, ADMIN, card.id, "https://github.com/vibepod/other/pull/1", github
    )
    assert (linked.github_pr_repository, linked.github_pr_number) == ("vibepod/other", 1)
    assert (linked.github_pr_state, linked.github_pr_base) == ("open", "develop")
    assert linked.github_pr_draft is True

    unlinked = pull_requests.link_pull_request(session, ADMIN, card.id, " ")
    assert unlinked.github_pr_url is None
    assert unlinked.github_pr_number is None
    assert unlinked.github_pr_state is None
    assert events(session, card)[:2] == [
        (TaskEventKind.PR_UNLINKED, "PR #1 unlinked by admin"),
        (TaskEventKind.PR_LINKED, "PR #1 linked by admin"),
    ]


def test_linking_an_already_merged_pr_moves_the_card_to_done(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = pr_ready_card(session, project)
    dependent = ideas.create_idea(
        session, ADMIN, project_id=project.id, title="Next", depends_on=[card.idea_id]
    )
    fake.add_pull(REPO, "feature/pr", state="closed", merged=True)

    linked = pull_requests.link_pull_request(
        session, ADMIN, card.id, f"https://github.com/{REPO}/pull/1", github
    )

    assert (linked.column, linked.github_pr_state) == (BoardColumn.DONE, "merged")
    assert events(session, card)[:2] == [
        (TaskEventKind.PR_MERGED, "PR #1 merged"),
        (TaskEventKind.PR_LINKED, "PR #1 linked by admin"),
    ]
    assert ideas.get_idea(session, ADMIN, dependent.id).blocked_by == []

    # Moved back by hand, neither a sync nor linking the same PR again moves it.
    board.update_card(session, ADMIN, card.id, column=BoardColumn.PR_READY)
    pull_requests.sync_pull_request(session, ADMIN, card.id, github)
    again = pull_requests.link_pull_request(
        session, ADMIN, card.id, f"https://github.com/{REPO}/pull/1", github
    )
    assert again.column == BoardColumn.PR_READY
    assert [kind for kind, _ in events(session, card)].count(TaskEventKind.PR_MERGED) == 1


def test_linking_an_already_closed_pr_records_it_and_leaves_the_card_in_place(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = pr_ready_card(session, project)
    fake.add_pull(REPO, "feature/pr", state="closed")

    linked = pull_requests.link_pull_request(
        session, ADMIN, card.id, f"https://github.com/{REPO}/pull/1", github
    )

    assert (linked.column, linked.github_pr_state) == (BoardColumn.PR_READY, "closed")
    assert events(session, card)[:2] == [
        (TaskEventKind.PR_CLOSED, "PR #1 closed without merging"),
        (TaskEventKind.PR_LINKED, "PR #1 linked by admin"),
    ]


def test_linking_an_open_pr_records_only_the_link(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = pr_ready_card(session, project)
    fake.add_pull(REPO, "feature/pr")

    linked = pull_requests.link_pull_request(
        session, ADMIN, card.id, f"https://github.com/{REPO}/pull/1", github
    )

    assert (linked.column, linked.github_pr_state) == (BoardColumn.PR_READY, "open")
    assert linked.github_pr_synced_at is not None
    assert events(session, card)[0] == (TaskEventKind.PR_LINKED, "PR #1 linked by admin")


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


# --- sync ---------------------------------------------------------------------------


def linked_card(
    session: Session, project, fake: FakeGitHub, github: GitHubClient, **pull
) -> BoardCard:
    """A card in PR ready on `feature/pr`, linked to a new PR for that branch."""
    card = pr_ready_card(session, project, title=pull.pop("card_title", "Open PRs"))
    remote = fake.add_pull(REPO, pull.pop("head", "feature/pr"), **pull)
    return pull_requests.link_pull_request(session, ADMIN, card.id, remote["html_url"], github)


def test_sync_stores_the_open_prs_title_description_and_state(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = linked_card(session, project, fake, github, title="Add sync", draft=True)
    fake.edit_pull(REPO, 1, title="Sync PRs", body="Pulls **state**.", draft=False, sha="a" * 40)
    fake.review(REPO, 1, "alice", "APPROVED")

    synced = pull_requests.sync_pull_request(session, ADMIN, card.id, github)

    assert (synced.github_pr_title, synced.github_pr_body) == ("Sync PRs", "Pulls **state**.")
    assert (synced.github_pr_state, synced.github_pr_draft) == ("open", False)
    assert (synced.github_pr_base, synced.github_pr_head) == ("main", "feature/pr")
    assert synced.github_pr_head_sha == "a" * 40
    assert synced.github_pr_merged_at is None
    assert synced.github_pr_review_decision == "approved"
    assert synced.github_pr_synced_at is not None
    assert (synced.column, synced.branch_name) == (BoardColumn.PR_READY, "feature/pr")
    # Nothing happened that the history keeps.
    assert events(session, card)[0] == (TaskEventKind.PR_LINKED, "PR #1 linked by admin")
    # Nothing is written back to GitHub.
    assert all(method == "GET" for method, _ in fake.requests)


def test_an_unchanged_pr_costs_one_request_and_leaves_the_card_alone(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = linked_card(session, project, fake, github)
    first = pull_requests.sync_pull_request(session, ADMIN, card.id, github)
    fake.requests.clear()

    second = pull_requests.sync_pull_request(session, ADMIN, card.id, github)

    assert fake.requests == [("GET", f"/repos/{REPO}/pulls/1")]
    assert second.updated_at == first.updated_at
    assert second.github_pr_synced_at > first.github_pr_synced_at


@pytest.mark.parametrize(
    ("reviews", "decision"),
    [
        ([], None),
        ([("alice", "COMMENTED")], None),
        ([("alice", "APPROVED"), ("bob", "CHANGES_REQUESTED")], "changes_requested"),
        ([("bob", "CHANGES_REQUESTED"), ("bob", "APPROVED")], "approved"),
        ([("bob", "CHANGES_REQUESTED"), ("bob", "COMMENTED")], "changes_requested"),
        ([("alice", "APPROVED"), ("alice", "DISMISSED")], None),
    ],
)
def test_sync_reads_the_review_decision(
    session: Session,
    project,
    fake: FakeGitHub,
    github: GitHubClient,
    reviews: list[tuple[str, str]],
    decision: str | None,
) -> None:
    card = linked_card(session, project, fake, github)
    pull_requests.sync_pull_request(session, ADMIN, card.id, github)
    for reviewer, state in reviews:
        fake.review(REPO, 1, reviewer, state)

    synced = pull_requests.sync_pull_request(session, ADMIN, card.id, github)
    assert synced.github_pr_review_decision == decision


def test_a_merged_pr_moves_the_card_to_done_and_unblocks_its_dependents(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = linked_card(session, project, fake, github)
    dependent = ideas.create_idea(
        session, ADMIN, project_id=project.id, title="Next", depends_on=[card.idea_id]
    )
    assert ideas.get_idea(session, ADMIN, dependent.id).blocked_by == [card.idea_id]
    fake.edit_pull(REPO, 1, merged=True)

    synced = pull_requests.sync_pull_request(session, ADMIN, card.id, github)

    assert synced.column == BoardColumn.DONE
    assert synced.github_pr_state == "merged"
    assert synced.github_pr_merged_at is not None
    assert events(session, card)[0] == (TaskEventKind.PR_MERGED, "PR #1 merged")
    assert ideas.get_idea(session, ADMIN, dependent.id).blocked_by == []

    # Moved back by hand, the card stays where it was put on the next sync.
    board.update_card(session, ADMIN, card.id, column=BoardColumn.PR_READY)
    again = pull_requests.sync_pull_request(session, ADMIN, card.id, github)
    assert again.column == BoardColumn.PR_READY
    assert [kind for kind, _ in events(session, card)].count(TaskEventKind.PR_MERGED) == 1


def test_a_merge_ends_the_open_reviews_of_a_card_in_review(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = linked_card(session, project, fake, github)
    board.update_card(session, ADMIN, card.id, column=BoardColumn.REVIEW)
    fake.edit_pull(REPO, 1, merged=True)

    synced = pull_requests.sync_pull_request(session, ADMIN, card.id, github)
    assert synced.column == BoardColumn.DONE


def test_a_pr_closed_without_merging_leaves_the_card_in_place(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = linked_card(session, project, fake, github)
    fake.edit_pull(REPO, 1, state="closed")

    synced = pull_requests.sync_pull_request(session, ADMIN, card.id, github)

    assert (synced.column, synced.github_pr_state) == (BoardColumn.PR_READY, "closed")
    assert events(session, card)[0] == (TaskEventKind.PR_CLOSED, "PR #1 closed without merging")

    fake.edit_pull(REPO, 1, state="open")
    reopened = pull_requests.sync_pull_request(session, ADMIN, card.id, github)
    assert reopened.github_pr_state == "open"
    assert events(session, card)[0] == (TaskEventKind.PR_REOPENED, "PR #1 reopened")


def test_the_cards_branch_follows_a_renamed_head_branch(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = linked_card(session, project, fake, github)
    fake.edit_pull(REPO, 1, head="issue-31")

    synced = pull_requests.sync_pull_request(session, ADMIN, card.id, github)

    assert (synced.branch_name, synced.github_pr_head) == ("issue-31", "issue-31")
    assert events(session, card)[0] == (
        TaskEventKind.BRANCH_RENAMED,
        "Branch follows PR #1: `feature/pr` renamed to `issue-31`",
    )


def test_the_branch_does_not_follow_a_fork(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = linked_card(session, project, fake, github, head="main", head_repository="someone/fork")
    synced = pull_requests.sync_pull_request(session, ADMIN, card.id, github)
    assert (synced.branch_name, synced.github_pr_head) == ("feature/pr", "main")


def test_sync_needs_a_linked_pr(session: Session, project, github: GitHubClient) -> None:
    card = pr_ready_card(session, project)
    with pytest.raises(Conflict, match="no linked pull request"):
        pull_requests.sync_pull_request(session, ADMIN, card.id, github)


def test_unlinking_clears_what_a_sync_pulled(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = linked_card(session, project, fake, github, body="Body")
    fake.review(REPO, 1, "alice", "APPROVED")
    pull_requests.sync_pull_request(session, ADMIN, card.id, github)

    unlinked = pull_requests.link_pull_request(session, ADMIN, card.id, "")
    assert (unlinked.github_pr_body, unlinked.github_pr_review_decision) == (None, None)
    assert unlinked.github_pr_head_sha is None


# --- sweep --------------------------------------------------------------------------


def test_the_sweep_syncs_open_prs_of_cards_in_pr_ready_and_review(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    merged = linked_card(session, project, fake, github, card_title="Merged", head="a")
    reviewed = linked_card(session, project, fake, github, card_title="Reviewed", head="b")
    board.update_card(session, ADMIN, reviewed.id, column=BoardColumn.REVIEW)
    planned = linked_card(session, project, fake, github, card_title="Rework", head="c")
    board.update_card(session, ADMIN, planned.id, column=BoardColumn.PLANNED)
    closed = linked_card(session, project, fake, github, card_title="Closed", head="d")
    fake.edit_pull(REPO, 4, state="closed")
    pull_requests.sync_pull_request(session, ADMIN, closed.id, github)
    for number in (1, 2, 3):
        fake.edit_pull(REPO, number, merged=True)
    fake.requests.clear()

    assert pull_requests.sync_due_pull_requests(session, github) == 2

    assert sorted(path for _, path in fake.requests if not path.endswith("/reviews")) == [
        f"/repos/{REPO}/pulls/1",
        f"/repos/{REPO}/pulls/2",
    ]
    assert board.get_card(session, ADMIN, merged.id).column == BoardColumn.DONE
    assert board.get_card(session, ADMIN, reviewed.id).column == BoardColumn.DONE
    assert board.get_card(session, ADMIN, planned.id).column == BoardColumn.PLANNED
    assert (TaskEventKind.PR_MERGED, "PR #1 merged") in events(session, merged)
    assert history.list_task_history(session, ADMIN, merged.idea_id)[0].actor == "GitHub sync"
    # Done and merged, nothing is left to sweep.
    assert pull_requests.due_pull_requests(session) == []


def test_the_sweep_stops_on_a_rate_limit_and_skips_other_failures(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    gone = linked_card(session, project, fake, github, card_title="Gone", head="a")
    kept = linked_card(session, project, fake, github, card_title="Kept", head="b")
    del fake.pulls[(REPO, 1)]
    fake.edit_pull(REPO, 2, title="Renamed")

    assert pull_requests.sync_due_pull_requests(session, github) == 1
    assert board.get_card(session, ADMIN, kept.id).github_pr_title == "Renamed"
    assert board.get_card(session, ADMIN, gone.id).github_pr_title == "PR"

    fake.status_override = 403
    fake.override_headers = {"x-ratelimit-remaining": "0", "retry-after": "120"}
    with pytest.raises(GitHubRateLimited) as raised:
        pull_requests.sync_due_pull_requests(session, github)
    assert raised.value.retry_after == 120
    assert len([r for r in fake.requests if r[1] == f"/repos/{REPO}/pulls/2"]) == 1 + 1


def test_rate_limits_are_told_apart_from_a_rejected_token(
    session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = linked_card(session, project, fake, github)
    fake.status_override = 429
    with pytest.raises(GitHubRateLimited, match="rate limit"):
        pull_requests.sync_pull_request(session, ADMIN, card.id, github)
    fake.status_override = 403
    with pytest.raises(GitHubUnavailable, match="rejected the token") as raised:
        pull_requests.sync_pull_request(session, ADMIN, card.id, github)
    assert not isinstance(raised.value, GitHubRateLimited)


def test_backs_off_doubling_up_to_an_hour() -> None:
    assert pull_requests.backoff_delay(300, 300, None) == 600
    assert pull_requests.backoff_delay(300, 600, None) == 1200
    assert pull_requests.backoff_delay(300, 3000, None) == 3600
    assert pull_requests.backoff_delay(300, 300, 5000) == 5000
    assert pull_requests.backoff_delay(300, 10, 0) == 300


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


def test_rest_syncs_a_linked_pr(
    github_api: TestClient, session: Session, project, fake: FakeGitHub, github: GitHubClient
) -> None:
    card = linked_card(session, project, fake, github)
    fake.edit_pull(REPO, 1, body="## Why\n\nBecause.", merged=True)

    response = github_api.post(f"/api/board/{card.id}/pull-request/sync")

    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert (item["column"], item["githubPrState"]) == ("done", "merged")
    assert item["githubPrBody"] == "## Why\n\nBecause."
    assert item["githubPrHeadSha"] == f"{1:040x}"


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

    fake.edit_pull(REPO, 1, head="issue-31")
    synced = await call(github_server_url, token, "sync_pull_request", id="APP-1")
    assert synced["item"]["branchName"] == "issue-31"
