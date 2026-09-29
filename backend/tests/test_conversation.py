"""The needs-input and rework loop: an agent asks, someone answers, a reviewer sends work back
with feedback, and all of it stays in the task history for the next run."""

from typing import Any

import pytest
from conftest import call, login
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.enums import BoardColumn, ReleaseOutcome, RunOutcome
from vibepod_board.errors import BadRequest, Conflict
from vibepod_board.services import (
    board,
    claims,
    conversation,
    history,
    ideas,
    projects,
    runs,
    tokens,
)

ADMIN = admin_access("admin")
RUNNER = "claude@laptop"
QUESTION = "Should the cache live in Redis or in Postgres?"


@pytest.fixture
def vp(session: Session):
    return projects.create_project(session, "VP", "VibePod")


def planned(session: Session, project, title: str, **fields: Any):
    task = ideas.create_idea(session, ADMIN, title=title, project_id=project.id, **fields)
    ideas.mark_ready(session, ADMIN, task.id)
    board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED)
    return task


def asked(session: Session, project):
    """A task whose run stopped with a question."""
    task = planned(session, project, "Add a cache")
    claims.claim_task(session, ADMIN, "VP", RUNNER, task=task.id)
    claims.release_task(session, ADMIN, task.id, RUNNER, ReleaseOutcome.NEEDS_INPUT, QUESTION)
    return task


def in_review(session: Session, project):
    task = planned(session, project, "Add a cache")
    claims.claim_task(session, ADMIN, "VP", RUNNER, task=task.id)
    claims.hand_over_task(session, ADMIN, task.id, RUNNER, branch_name="issue-7")
    return task


def conversation_of(session: Session, task) -> list[tuple[str, str]]:
    events = history.list_task_history(session, ADMIN, task.id)
    return [
        (event.kind, event.message)
        for event in reversed(events)
        if event.kind in {"question", "answer", "feedback"}
    ]


def test_needs_input_blocks_the_task_with_the_question(session: Session, vp) -> None:
    task = asked(session, vp)

    card = board.get_card(session, ADMIN, task.id)
    assert card.column == BoardColumn.PLANNED
    assert card.question == QUESTION
    assert card.blocked_reason == f"Needs input: {QUESTION}"
    assert card.attempts == 0
    event = history.list_task_history(session, ADMIN, task.id)[0]
    assert (event.kind, event.message, event.actor) == ("question", QUESTION, RUNNER)
    assert claims.claim_task(session, ADMIN, "VP", RUNNER).claimed is False


def test_asking_needs_a_question(session: Session, vp) -> None:
    task = planned(session, vp, "Unclear")
    claims.claim_task(session, ADMIN, "VP", RUNNER)
    with pytest.raises(BadRequest, match="it is the question on the card"):
        claims.release_task(session, ADMIN, task.id, RUNNER, ReleaseOutcome.NEEDS_INPUT)


def test_an_answer_puts_the_task_back_in_planned(session: Session, vp) -> None:
    task = asked(session, vp)

    answered = conversation.answer_question(session, ADMIN, "VP-1", "Postgres, no new services.")

    assert answered.column == BoardColumn.PLANNED
    assert (answered.question, answered.blocked_at, answered.blocked_reason) == (None, None, None)
    assert conversation_of(session, task) == [
        ("question", QUESTION),
        ("answer", "Postgres, no new services."),
    ]
    assert history.list_task_history(session, ADMIN, task.id)[0].actor == "admin"
    assert claims.claim_task(session, ADMIN, "VP", RUNNER).item.task.key == "VP-1"


def test_only_a_waiting_question_can_be_answered(session: Session, vp) -> None:
    planned(session, vp, "Nothing asked")
    with pytest.raises(Conflict, match="VP-1 has no question to answer"):
        conversation.answer_question(session, ADMIN, "VP-1", "Anyway")
    asked_task = planned(session, vp, "Asked")
    claims.claim_task(session, ADMIN, "VP", RUNNER, task=asked_task.id)
    claims.release_task(session, ADMIN, asked_task.id, RUNNER, ReleaseOutcome.NEEDS_INPUT, QUESTION)
    with pytest.raises(BadRequest, match="An answer is required"):
        conversation.answer_question(session, ADMIN, asked_task.id, "  ")


def test_unblocking_by_hand_drops_the_question(session: Session, vp) -> None:
    task = asked(session, vp)

    unblocked = board.update_card(session, ADMIN, task.id, column=BoardColumn.PLANNED)

    assert unblocked.question is None
    assert unblocked.blocked_at is None
    # The question stays in the history.
    assert conversation_of(session, task) == [("question", QUESTION)]


def test_rework_sends_a_reviewed_task_back_with_feedback_and_keeps_its_branch(
    session: Session, vp
) -> None:
    task = in_review(session, vp)

    sent_back = conversation.request_rework(
        session, ADMIN, task.id, "Invalidate the cache when a task is deleted."
    )

    assert sent_back.column == BoardColumn.PLANNED
    assert sent_back.branch_name == "issue-7"
    assert sent_back.attempts == 0
    assert conversation_of(session, task) == [
        ("feedback", "Invalidate the cache when a task is deleted.")
    ]
    claimed = claims.claim_task(session, ADMIN, "VP", RUNNER)
    assert claimed.item.card.branch_name == "issue-7"


def test_rework_needs_a_task_in_review_and_feedback(session: Session, vp) -> None:
    planned(session, vp, "Not reviewed yet")
    with pytest.raises(Conflict, match="VP-1 is in planned; only tasks in review"):
        conversation.request_rework(session, ADMIN, "VP-1", "Change it")
    reviewed = in_review(session, vp)
    with pytest.raises(BadRequest, match="Feedback is required"):
        conversation.request_rework(session, ADMIN, reviewed.id, " ")


def test_the_whole_loop_stays_in_the_history(session: Session, vp) -> None:
    task = asked(session, vp)
    conversation.answer_question(session, ADMIN, task.id, "Postgres.")
    claims.claim_task(session, ADMIN, "VP", RUNNER)
    claims.hand_over_task(session, ADMIN, task.id, RUNNER, branch_name="issue-7")
    conversation.request_rework(session, ADMIN, task.id, "Add a test for eviction.")

    assert conversation_of(session, task) == [
        ("question", QUESTION),
        ("answer", "Postgres."),
        ("feedback", "Add a test for eviction."),
    ]


def test_a_run_report_can_say_the_run_needs_input(session: Session, vp) -> None:
    task = planned(session, vp, "Unclear")
    report = runs.add_run_report(
        session, ADMIN, task.id, RunOutcome.NEEDS_INPUT, failure_reason=QUESTION
    )
    assert report.outcome == RunOutcome.NEEDS_INPUT


# --- REST ----------------------------------------------------------------------------


@pytest.fixture
def api(client: TestClient) -> TestClient:
    login(client)
    assert client.post("/api/projects", json={"key": "VP", "title": "VibePod"}).status_code == 201
    task = client.post("/api/ideas", json={"projectId": "VP", "title": "Over REST"}).json()
    client.post(f"/api/ideas/{task['item']['id']}/ready")
    client.patch(f"/api/board/{task['item']['id']}", json={"column": "planned"})
    return client


def test_rest_runs_the_loop(api: TestClient) -> None:
    api.post("/api/board/claim", json={"projectId": "VP", "assignee": RUNNER})
    released = api.post(
        "/api/board/VP-1/release",
        json={"assignee": RUNNER, "outcome": "needs_input", "note": QUESTION},
    )
    assert released.status_code == 200, released.text
    assert released.json()["item"]["question"] == QUESTION

    answered = api.post("/api/board/VP-1/answer", json={"answer": "Postgres."})
    assert answered.status_code == 200
    assert "question" not in answered.json()["item"]
    assert api.post("/api/board/VP-1/answer", json={"answer": "Again"}).status_code == 409
    assert api.post("/api/board/VP-1/answer", json={"answer": ""}).status_code == 400

    api.post("/api/board/claim", json={"projectId": "VP", "assignee": RUNNER})
    api.post("/api/board/VP-1/handover", json={"assignee": RUNNER, "branchName": "vp-1"})
    reworked = api.post("/api/board/VP-1/rework", json={"feedback": "Needs a test."})
    assert reworked.status_code == 200
    assert (reworked.json()["item"]["column"], reworked.json()["item"]["branchName"]) == (
        "planned",
        "vp-1",
    )
    kinds = [event["kind"] for event in api.get("/api/ideas/VP-1/history").json()["items"]]
    assert [kind for kind in kinds if kind in {"question", "answer", "feedback"}] == [
        "feedback",
        "answer",
        "question",
    ]


# --- MCP -----------------------------------------------------------------------------


async def test_mcp_runs_the_loop(server_url: str, session: Session, vp) -> None:
    task = planned(session, vp, "Over MCP")
    token = tokens.create_token(session, "Runner", [vp.id]).token

    await call(server_url, token, "claim_next_task", projectId="VP", assignee=RUNNER)
    released = await call(
        server_url,
        token,
        "release_task",
        id="VP-1",
        assignee=RUNNER,
        outcome="needs_input",
        note=QUESTION,
    )
    assert released["item"]["question"] == QUESTION

    answered = await call(server_url, token, "answer_task_question", id="VP-1", answer="Redis")
    assert answered["item"]["column"] == "planned"

    with pytest.raises(ToolError, match="only tasks in review are sent back"):
        await call(server_url, token, "request_task_rework", id=task.id, feedback="Nope")
    await call(server_url, token, "claim_next_task", projectId="VP", assignee=RUNNER)
    await call(server_url, token, "hand_over_task", id="VP-1", assignee=RUNNER, branchName="b")
    reworked = await call(
        server_url, token, "request_task_rework", id="VP-1", feedback="Try again", view="full"
    )
    assert (reworked["item"]["column"], reworked["item"]["branchName"]) == ("planned", "b")
