"""Push requests: the approval that completes a task's approvals asks its reviewer to push the
branch, Open PR asks any worker, leases lapse back to pending, and a push opens the PR."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta

import pytest
from conftest import call, login, serve
from fastapi.testclient import TestClient
from github_fake import FakeGitHub
from sqlalchemy import Engine, update
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.config import Settings
from vibepod_board.enums import (
    BoardColumn,
    ClaimMode,
    PushReason,
    PushStatus,
    ReviewVerdict,
    TaskEventKind,
    WorkerStatus,
)
from vibepod_board.errors import BadRequest, Conflict, NotFound
from vibepod_board.github import GitHubClient
from vibepod_board.main import create_app
from vibepod_board.schemas import now
from vibepod_board.services import (
    board,
    claims,
    conversation,
    history,
    ideas,
    projects,
    pull_requests,
    push_requests,
    reviews,
    tokens,
    workers,
)
from vibepod_board.tables import PushRequestRow

ADMIN = admin_access("admin")
RUNNER = "Claude::Runner"
REVIEWER = "Codex::Reviewer"
OTHER = "Claude::Other"
SHA = "a" * 40
PUSHED = "b" * 40
REPO = "vibepod/board"
REMOTE = "https://github.com/vibepod/board"
LOCAL = "/home/me/board"


@pytest.fixture
def vp(session: Session):
    return projects.create_project(session, "VP", "VibePod")


def in_review(session: Session, project, title: str = "Push it", branch: str = "vp-1"):
    """A task an implementation run handed over to Review at SHA."""
    task = ideas.create_idea(
        session,
        ADMIN,
        title=title,
        project_id=project.id,
        repository_local_path=LOCAL,
        repository_remote_url=REMOTE,
    )
    ideas.mark_ready(session, ADMIN, task.id)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED)
    claims.claim_task(session, ADMIN, project.key, RUNNER, task=task.id)
    claims.hand_over_task(session, ADMIN, task.id, RUNNER, branch_name=branch, head_sha=SHA)
    return task


def approve(session: Session, task, reviewer: str = REVIEWER, project: str = "VP"):
    claims.claim_task(session, ADMIN, project, reviewer, mode=ClaimMode.REVIEW, task=task.id)
    return reviews.submit_review(session, ADMIN, task.id, reviewer, ReviewVerdict.APPROVE, SHA)


def lapse(session: Session, request_id: str) -> None:
    session.execute(
        update(PushRequestRow)
        .where(PushRequestRow.id == request_id)
        .values(lease_expires_at=now() - timedelta(seconds=1))
    )
    session.commit()


def kinds(session: Session, task) -> list[str]:
    return [event.kind for event in history.list_task_history(session, ADMIN, task.id)]


# --- on approval ---------------------------------------------------------------------


def test_the_approval_to_pr_ready_asks_its_reviewer_to_push(session: Session, vp) -> None:
    task = in_review(session, vp)

    state = approve(session, task)

    request = state.push_request
    assert request is not None
    assert state.column == BoardColumn.PR_READY
    assert (request.reason, request.status, request.assignee) == (
        PushReason.APPROVED,
        PushStatus.CLAIMED,
        REVIEWER,
    )
    assert (request.branch, request.head_sha, request.task_key) == ("vp-1", SHA, "VP-1")
    assert (request.repository, request.repository_local_path) == (REPO, LOCAL)
    assert request.repository_remote_url == REMOTE
    assert request.lease_expires_at is not None
    assert timedelta(minutes=4) < request.lease_expires_at - now() <= timedelta(minutes=5)
    assert TaskEventKind.PUSH_REQUESTED in kinds(session, task)

    pushed = push_requests.report_result(
        session, ADMIN, request.id, REVIEWER, PushStatus.PUSHED, pushed_sha=PUSHED
    )

    assert (pushed.status, pushed.pushed_sha, pushed.assignee) == (
        PushStatus.PUSHED,
        PUSHED,
        REVIEWER,
    )
    card = board.get_card(session, ADMIN, task.id)
    assert card.branch_pushed_sha == PUSHED
    assert card.branch_pushed_at is not None
    assert TaskEventKind.BRANCH_PUSHED in kinds(session, task)
    with pytest.raises(Conflict, match="not held by"):
        push_requests.report_result(session, ADMIN, request.id, REVIEWER, PushStatus.PUSHED)


def test_only_the_approval_that_completes_the_requirement_asks(session: Session, vp) -> None:
    projects.update_project_settings(session, ADMIN, "VP", 2)
    task = in_review(session, vp)

    first = approve(session, task, OTHER)
    second = approve(session, task)

    assert first.push_request is None
    assert first.model_dump()["pushRequest"] is None
    assert second.push_request is not None and second.push_request.assignee == REVIEWER


def assert_open_to_any_worker(session: Session, task, request) -> None:
    assert (request.reason, request.status, request.assignee) == (
        PushReason.APPROVED,
        PushStatus.PENDING,
        None,
    )
    assert (request.branch, request.head_sha, request.lease_expires_at) == ("vp-1", SHA, None)
    worker = workers.register_worker(session, ADMIN, "VP", OTHER).item
    beat = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE)
    assert [item.id for item in beat.push_requests] == [request.id]


def test_an_approval_on_the_board_asks_any_worker_to_push(session: Session, vp) -> None:
    task = in_review(session, vp)

    card = conversation.approve_review(session, ADMIN, task.id)

    assert card.column == BoardColumn.PR_READY
    assert_open_to_any_worker(
        session, task, push_requests.latest_push_request(session, ADMIN, task.id)
    )
    assert TaskEventKind.PUSH_REQUESTED in kinds(session, task)


def test_a_move_to_pr_ready_by_hand_asks_once(session: Session, vp) -> None:
    task = in_review(session, vp)

    board.update_card(session, ADMIN, task.id, column=BoardColumn.PR_READY)
    first = push_requests.latest_push_request(session, ADMIN, task.id)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.DONE)
    board.update_cards(session, ADMIN, [{"id": task.id, "column": BoardColumn.PR_READY}])

    assert_open_to_any_worker(session, task, first)
    assert push_requests.latest_push_request(session, ADMIN, task.id).id == first.id
    assert kinds(session, task).count(TaskEventKind.PUSH_REQUESTED) == 1


def test_a_lowered_requirement_asks_for_the_cards_it_promotes(session: Session, vp) -> None:
    projects.update_project_settings(session, ADMIN, "VP", 2)
    task = in_review(session, vp)
    assert approve(session, task).push_request is None

    projects.update_project_settings(session, ADMIN, "VP", 1)

    assert board.get_card(session, ADMIN, task.id).column == BoardColumn.PR_READY
    assert_open_to_any_worker(
        session, task, push_requests.latest_push_request(session, ADMIN, task.id)
    )


def test_pr_ready_by_hand_supersedes_a_held_push_of_an_older_commit(session: Session, vp) -> None:
    task = in_review(session, vp)
    held = approve(session, task).push_request
    assert held is not None
    session.execute(
        update(PushRequestRow).where(PushRequestRow.id == held.id).values(head_sha=PUSHED)
    )
    session.commit()
    board.update_card(session, ADMIN, task.id, column=BoardColumn.REVIEW)

    board.update_card(session, ADMIN, task.id, column=BoardColumn.PR_READY)

    latest = push_requests.latest_push_request(session, ADMIN, task.id)
    assert latest.id != held.id
    assert_open_to_any_worker(session, task, latest)
    with pytest.raises(Conflict, match="already ended"):
        push_requests.claim_push_request(session, ADMIN, held.id, REVIEWER)


def test_pr_ready_asks_nothing_when_the_head_is_pushed_or_pushing_is_off(
    session: Session, vp
) -> None:
    pushed = in_review(session, vp)
    request = approve(session, pushed).push_request
    assert request is not None
    push_requests.report_result(session, ADMIN, request.id, REVIEWER, PushStatus.PUSHED)
    board.update_card(session, ADMIN, pushed.id, column=BoardColumn.REVIEW)
    board.update_card(session, ADMIN, pushed.id, column=BoardColumn.PR_READY)
    assert push_requests.latest_push_request(session, ADMIN, pushed.id).id == request.id

    projects.update_project_settings(session, ADMIN, "VP", push_approved_branches=False)
    off = in_review(session, vp, "Off", branch="vp-2")
    conversation.approve_review(session, ADMIN, off.id)
    with pytest.raises(NotFound):
        push_requests.latest_push_request(session, ADMIN, off.id)


def test_projects_can_turn_pushing_approved_branches_off(session: Session, vp) -> None:
    assert vp.push_approved_branches is True
    updated = projects.update_project_settings(session, ADMIN, "VP", push_approved_branches=False)
    assert updated.push_approved_branches is False
    task = in_review(session, vp)

    assert approve(session, task).push_request is None
    with pytest.raises(NotFound):
        push_requests.latest_push_request(session, ADMIN, task.id)


def test_a_reported_failure_ends_the_request(session: Session, vp) -> None:
    task = in_review(session, vp)
    request = approve(session, task).push_request
    assert request is not None

    failed = push_requests.report_result(
        session,
        ADMIN,
        request.id,
        REVIEWER,
        PushStatus.FAILED,
        error="! [rejected] non-fast-forward",
    )

    assert (failed.status, failed.error) == (PushStatus.FAILED, "! [rejected] non-fast-forward")
    assert board.get_card(session, ADMIN, task.id).branch_pushed_sha is None
    assert TaskEventKind.PUSH_FAILED in kinds(session, task)
    with pytest.raises(Conflict, match="already ended"):
        push_requests.claim_push_request(session, ADMIN, request.id, OTHER)
    # A finished request leaves room for a new one.
    again = push_requests.request_push(session, ADMIN, task.id)
    assert again.id != request.id and again.status == PushStatus.PENDING


# --- leases --------------------------------------------------------------------------


def test_a_held_request_opens_to_other_workers_once_its_lease_lapses(session: Session, vp) -> None:
    task = in_review(session, vp)
    request = approve(session, task).push_request
    assert request is not None
    other = workers.register_worker(session, ADMIN, "VP", OTHER).item

    with pytest.raises(Conflict, match=f"held by {REVIEWER}"):
        push_requests.claim_push_request(session, ADMIN, request.id, OTHER)
    beat = workers.heartbeat(session, ADMIN, other.id, WorkerStatus.IDLE)
    assert beat.push_requests == []

    lapse(session, request.id)
    beat = workers.heartbeat(session, ADMIN, other.id, WorkerStatus.IDLE)

    assert [(item.id, item.status, item.assignee) for item in beat.push_requests] == [
        (request.id, PushStatus.PENDING, None)
    ]
    claimed = push_requests.claim_push_request(session, ADMIN, request.id, OTHER)
    assert (claimed.status, claimed.assignee) == (PushStatus.CLAIMED, OTHER)
    with pytest.raises(Conflict, match="not held by"):
        push_requests.report_result(session, ADMIN, request.id, REVIEWER, PushStatus.PUSHED)
    # Claiming again renews the holder's lease.
    renewed = push_requests.claim_push_request(session, ADMIN, request.id, OTHER)
    assert renewed.assignee == OTHER


def test_a_lapsed_lease_is_swept_when_the_request_is_read(session: Session, vp) -> None:
    task = in_review(session, vp)
    request = approve(session, task).push_request
    assert request is not None
    lapse(session, request.id)

    latest = push_requests.latest_push_request(session, ADMIN, task.id)

    assert (latest.status, latest.assignee, latest.lease_expires_at) == (
        PushStatus.PENDING,
        None,
        None,
    )


def test_heartbeats_offer_the_oldest_pending_requests_of_the_project(session: Session, vp) -> None:
    projects.update_project_settings(session, ADMIN, "VP", push_approved_branches=False)
    tasks = [in_review(session, vp, f"Task {n}", branch=f"vp-{n}") for n in range(7)]
    for task in tasks:
        approve(session, task)
        push_requests.request_push(session, ADMIN, task.id)
    elsewhere = projects.create_project(session, "EL", "Elsewhere")
    foreign = in_review(session, elsewhere, "Foreign")
    approve(session, foreign, project="EL")
    push_requests.request_push(session, ADMIN, foreign.id)
    worker = workers.register_worker(session, ADMIN, "VP", OTHER).item

    beat = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE)

    assert [item.branch for item in beat.push_requests] == [f"vp-{n}" for n in range(5)]
    push_requests.claim_push_request(session, ADMIN, beat.push_requests[0].id, OTHER)
    beat = workers.heartbeat(session, ADMIN, worker.id, WorkerStatus.IDLE)
    assert [item.branch for item in beat.push_requests] == [f"vp-{n}" for n in range(1, 6)]


# --- requests from Open PR ------------------------------------------------------------


def test_a_card_has_one_open_request_at_a_time(session: Session, vp) -> None:
    projects.update_project_settings(session, ADMIN, "VP", push_approved_branches=False)
    task = in_review(session, vp)
    approve(session, task)

    first = push_requests.request_push(session, ADMIN, task.id)
    second = push_requests.request_push(session, ADMIN, task.id, {"title": "Ship it"})

    assert second.id == first.id
    assert (first.reason, first.status, first.head_sha) == (
        PushReason.OPEN_PR,
        PushStatus.PENDING,
        SHA,
    )
    assert push_requests.latest_push_request(session, ADMIN, task.id).id == first.id


def handed_over_again(session: Session, task, head: str) -> None:
    """Sends the task back for rework and hands it over again, to Review, at `head`."""
    conversation.request_rework(session, ADMIN, task.id, "One more thing")
    claims.claim_task(session, ADMIN, "VP", RUNNER, task=task.id)
    claims.hand_over_task(session, ADMIN, task.id, RUNNER, branch_name="vp-1", head_sha=head)


def test_open_pr_pushes_the_handed_over_commit(
    github_api: TestClient, session: Session, vp
) -> None:
    projects.update_project_settings(session, ADMIN, "VP", push_approved_branches=False)
    task = in_review(session, vp)
    approve(session, task)
    card_id = board.get_card(session, ADMIN, task.id).id
    token = tokens.create_token(session, "Worker", [vp.id]).token

    created = github_api.post(
        f"/api/board/{card_id}/push-request",
        json={"openPr": {"title": "Ship it", "base": "main"}},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    assert created["headSha"] == SHA
    messages = [event.message for event in history.list_task_history(session, ADMIN, task.id)]
    assert any(message.startswith(f"Push of vp-1 at {SHA[:12]} requested") for message in messages)


def test_a_pending_request_follows_the_card_to_its_new_head(session: Session, vp) -> None:
    projects.update_project_settings(session, ADMIN, "VP", push_approved_branches=False)
    task = in_review(session, vp)
    approve(session, task)
    first = push_requests.request_push(session, ADMIN, task.id)
    handed_over_again(session, task, PUSHED)

    again = push_requests.request_push(session, ADMIN, task.id)

    assert (again.id, again.status, again.head_sha) == (first.id, PushStatus.PENDING, PUSHED)


def test_a_held_request_for_an_older_commit_is_not_returned_as_the_head(
    session: Session, vp
) -> None:
    projects.update_project_settings(session, ADMIN, "VP", push_approved_branches=False)
    task = in_review(session, vp)
    approve(session, task)
    first = push_requests.request_push(session, ADMIN, task.id)
    push_requests.claim_push_request(session, ADMIN, first.id, OTHER)
    handed_over_again(session, task, PUSHED)

    with pytest.raises(Conflict, match=f"{OTHER} is pushing vp-1 at {SHA[:12]}"):
        push_requests.request_push(session, ADMIN, task.id)
    push_requests.report_result(session, ADMIN, first.id, OTHER, PushStatus.PUSHED)
    assert push_requests.request_push(session, ADMIN, task.id).head_sha == PUSHED


def test_requests_need_a_branch_and_pull_requests_need_pr_ready(session: Session, vp) -> None:
    task = ideas.create_idea(session, ADMIN, title="No branch", project_id=vp.id)
    ideas.mark_ready(session, ADMIN, task.id)
    with pytest.raises(Conflict, match="branch"):
        push_requests.request_push(session, ADMIN, task.id)
    reviewed = in_review(session, vp, "In review")
    with pytest.raises(Conflict, match="PR ready"):
        push_requests.request_push(session, ADMIN, reviewed.id, {"title": "Ship it"})
    approve(session, reviewed)
    with pytest.raises(BadRequest, match="title"):
        push_requests.request_push(session, ADMIN, reviewed.id, {"title": " "})


@pytest.fixture
def fake() -> FakeGitHub:
    github = FakeGitHub()
    github.branches[REPO] = ["main"]
    return github


@pytest.fixture
def github(fake: FakeGitHub) -> GitHubClient:
    return GitHubClient("token", fake.transport)


def open_pr_request(session: Session, vp):
    projects.update_project_settings(session, ADMIN, "VP", push_approved_branches=False)
    task = in_review(session, vp)
    approve(session, task)
    options = {"title": "Push and open", "body": "Body", "base": "main", "draft": True}
    request = push_requests.request_push(session, ADMIN, task.id, options)
    push_requests.claim_push_request(session, ADMIN, request.id, OTHER)
    return task, request


def test_a_push_opens_the_pull_request_it_was_asked_for(
    session: Session, vp, fake: FakeGitHub, github: GitHubClient
) -> None:
    task, request = open_pr_request(session, vp)
    draft = pull_requests.pull_request_draft(session, ADMIN, task.id, github)
    assert draft.branch_on_github is False
    fake.branches[REPO].append("vp-1")

    pushed = push_requests.report_result(
        session, ADMIN, request.id, OTHER, PushStatus.PUSHED, pushed_sha=SHA, client=github
    )

    assert pushed.status == PushStatus.PUSHED and pushed.error is None
    card = board.get_card(session, ADMIN, task.id)
    assert pushed.pull_request_url == card.github_pr_url
    assert (card.github_pr_number, card.github_pr_draft, card.branch_pushed_sha) == (1, True, SHA)
    assert fake.pulls[(REPO, 1)]["title"] == "Push and open"
    assert TaskEventKind.PR_OPENED in kinds(session, task)


def test_a_failed_pull_request_keeps_the_push(session: Session, vp) -> None:
    task, request = open_pr_request(session, vp)

    pushed = push_requests.report_result(
        session, ADMIN, request.id, OTHER, PushStatus.PUSHED, pushed_sha=SHA, client=None
    )

    assert pushed.status == PushStatus.PUSHED
    assert pushed.pull_request_url is None
    assert pushed.error is not None and "GITHUB_TOKEN" in pushed.error
    assert board.get_card(session, ADMIN, task.id).branch_pushed_sha == SHA


# --- REST and MCP --------------------------------------------------------------------


@pytest.fixture
def github_api(db: Engine, settings: Settings, fake: FakeGitHub) -> Iterator[TestClient]:
    configured = replace(settings, github_token="token")
    app = create_app(configured, run_migrations=False, github_transport=fake.transport)
    with TestClient(app) as test_client:
        yield test_client


def test_rest_requests_claims_and_reports_pushes(
    github_api: TestClient, session: Session, vp, fake: FakeGitHub
) -> None:
    projects.update_project_settings(session, ADMIN, "VP", push_approved_branches=False)
    task = in_review(session, vp)
    approve(session, task)
    card_id = board.get_card(session, ADMIN, task.id).id
    token = tokens.create_token(session, "Worker", [vp.id]).token
    headers = {"Authorization": f"Bearer {token}"}

    assert github_api.get(f"/api/board/{card_id}/push-request", headers=headers).status_code == 404
    draft = github_api.get(f"/api/board/{card_id}/pull-request", headers=headers).json()
    assert draft["branchOnGithub"] is False
    created = github_api.post(
        f"/api/board/{card_id}/push-request",
        json={"openPr": {"title": "From REST", "base": "main"}},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    request = created.json()
    assert request["reason"] == "open_pr" and request["status"] == "pending"
    assert request["assignee"] is None and request["taskKey"] == "VP-1"
    worker = github_api.post(
        "/api/workers", json={"projectId": "VP", "name": OTHER}, headers=headers
    ).json()["item"]
    beat = github_api.post(
        f"/api/workers/{worker['id']}/heartbeat", json={"status": "idle"}, headers=headers
    ).json()
    assert [item["id"] for item in beat["pushRequests"]] == [request["id"]]

    claimed = github_api.post(
        f"/api/board/push-requests/{request['id']}/claim",
        json={"assignee": OTHER},
        headers=headers,
    )
    assert claimed.status_code == 200 and claimed.json()["status"] == "claimed"
    refused = github_api.post(
        f"/api/board/push-requests/{request['id']}/claim",
        json={"assignee": REVIEWER},
        headers=headers,
    )
    assert refused.status_code == 409
    fake.branches[REPO].append("vp-1")
    reported = github_api.post(
        f"/api/board/push-requests/{request['id']}/result",
        json={"assignee": OTHER, "status": "pushed", "pushedSha": PUSHED},
        headers=headers,
    )
    assert reported.status_code == 200, reported.text
    assert reported.json()["pullRequestUrl"] == f"https://github.com/{REPO}/pull/1"
    latest = github_api.get(f"/api/board/{card_id}/push-request", headers=headers).json()
    assert (latest["status"], latest["pushedSha"]) == ("pushed", PUSHED)
    card = github_api.get(f"/api/board/{card_id}", headers=headers).json()["item"]
    assert card["branchPushedSha"] == PUSHED and card["branchPushedAt"]
    invalid = github_api.post(
        f"/api/board/push-requests/{request['id']}/result",
        json={"assignee": OTHER, "status": "claimed"},
        headers=headers,
    )
    assert invalid.status_code == 400


def test_rest_review_verdict_and_settings(client: TestClient, session: Session, vp) -> None:
    task = in_review(session, vp)
    card_id = board.get_card(session, ADMIN, task.id).id
    token = tokens.create_token(session, "Reviewer", [vp.id]).token
    headers = {"Authorization": f"Bearer {token}"}
    claims.claim_task(session, ADMIN, "VP", REVIEWER, mode=ClaimMode.REVIEW, task=task.id)

    verdict = client.post(
        f"/api/board/{card_id}/review",
        json={"assignee": REVIEWER, "verdict": "approve", "headSha": SHA},
        headers=headers,
    ).json()

    assert verdict["pushRequest"]["assignee"] == REVIEWER
    assert verdict["pushRequest"]["headSha"] == SHA
    assert verdict["pushRequest"]["pushedSha"] is None
    settings = client.patch(
        f"/api/projects/{vp.id}/settings", json={"pushApprovedBranches": False}, headers=headers
    )
    assert settings.json()["item"]["pushApprovedBranches"] is False
    assert (
        client.patch(
            f"/api/projects/{vp.id}/settings", json={"pushApprovedBranches": "no"}, headers=headers
        ).status_code
        == 400
    )
    login(client)
    edited = client.patch(f"/api/projects/{vp.id}", json={"pushApprovedBranches": True})
    assert edited.json()["item"]["pushApprovedBranches"] is True


def test_rest_approval_on_the_board_asks_for_a_push(
    client: TestClient, session: Session, vp
) -> None:
    task = in_review(session, vp)
    card_id = board.get_card(session, ADMIN, task.id).id
    login(client)

    approved = client.post(f"/api/board/{card_id}/approve", json={})

    assert approved.status_code == 200, approved.text
    request = client.get(f"/api/board/{card_id}/push-request").json()
    assert (request["reason"], request["status"], request["assignee"], request["headSha"]) == (
        "approved",
        "pending",
        None,
        SHA,
    )


@pytest.fixture
def server_url(db: Engine, settings: Settings) -> Iterator[str]:
    with serve(create_app(settings, run_migrations=False)) as url:
        yield url


async def test_mcp_requests_and_reads_pushes(server_url: str, session: Session, vp) -> None:
    projects.update_project_settings(session, ADMIN, "VP", push_approved_branches=False)
    task = in_review(session, vp)
    approve(session, task)
    token = tokens.create_token(session, "Agent", [vp.id]).token

    created = await call(
        server_url, token, "request_branch_push", id="VP-1", openPr={"draft": True}
    )
    latest = await call(server_url, token, "get_push_request", id="VP-1")

    assert created["reason"] == "open_pr" and created["status"] == "pending"
    assert latest["id"] == created["id"]
    settings = await call(
        server_url, token, "update_project_settings", projectId="VP", pushApprovedBranches=False
    )
    assert settings["item"]["pushApprovedBranches"] is False
