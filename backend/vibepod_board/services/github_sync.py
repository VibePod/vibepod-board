"""Push a task to its GitHub issue, pull the issue back, and import issues from agents.

Rules shared by all three: the task keeps the remote identity and sync state; details are
never overwritten once written (they hold local refinement); status and board column are
never changed by GitHub state — a closed issue only shows as a badge.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlmodel import Session, select

from vibepod_board.access import (
    AccessContext,
    assert_can_access_project,
    default_project_id_for_create,
)
from vibepod_board.enums import IdeaStatus
from vibepod_board.errors import BadRequest, Conflict
from vibepod_board.github import (
    GitHubClient,
    IssueRef,
    RemoteIssue,
    issue_body,
    repository_from_remote,
)
from vibepod_board.schemas import ApiModel, Idea, now
from vibepod_board.services.board import sync_card_from_idea
from vibepod_board.services.common import (
    add_activity,
    assert_title,
    new_id,
    require_idea,
    transactional,
)
from vibepod_board.services.dependencies import decorate_idea
from vibepod_board.services.github_link import apply_remote
from vibepod_board.services.ideas import next_task_number
from vibepod_board.services.projects import resolve_project_id_for_create
from vibepod_board.tables import IdeaRow

STALE_MESSAGE = "Issue changed on GitHub since the last sync — pull first"


class UpsertResult(ApiModel):
    item: Idea
    created: bool
    unchanged: bool


def target_repository(idea: IdeaRow, default_repository: str | None) -> str:
    repository = (
        idea.github_repository
        or repository_from_remote(idea.repository_remote_url)
        or default_repository
    )
    if not repository:
        raise BadRequest("No GitHub repository for this task")
    return repository


def _linked_ref(idea: IdeaRow) -> IssueRef | None:
    if idea.github_repository and idea.github_issue_number:
        return IssueRef(idea.github_repository, idea.github_issue_number)
    return None


def _finish(session: Session, idea: IdeaRow, timestamp: datetime, message: str) -> Idea:
    session.flush()
    sync_card_from_idea(session, idea, timestamp)
    add_activity(session, "idea.github", message, timestamp)
    return decorate_idea(session, idea)


@transactional
def push(
    session: Session,
    access: AccessContext,
    idea_id: str,
    client: GitHubClient,
    default_repository: str | None,
) -> Idea:
    idea = require_idea(session, idea_id)
    assert_can_access_project(access, idea.project_id)
    body = issue_body(idea.summary, idea.details, list(idea.acceptance_criteria))
    ref = _linked_ref(idea)

    if ref is None:
        issue = client.create_issue(
            target_repository(idea, default_repository), idea.title, body, list(idea.labels)
        )
        verb = "Created"
    else:
        remote = client.get_issue(ref)
        synced = idea.github_issue_updated_at
        if synced is None or remote.updated_at > synced:
            raise Conflict(STALE_MESSAGE)
        issue = client.update_issue(ref, idea.title, body, list(idea.labels))
        verb = "Updated"

    timestamp = now()
    apply_remote(idea, issue, timestamp, content=False)
    idea.updated_at = timestamp
    return _finish(session, idea, timestamp, f"{verb} GitHub issue {issue.ref}: {idea.title}")


@transactional
def pull(session: Session, access: AccessContext, idea_id: str, client: GitHubClient) -> Idea:
    idea = require_idea(session, idea_id)
    assert_can_access_project(access, idea.project_id)
    ref = _linked_ref(idea)
    if ref is None:
        raise Conflict("Task is not linked to a GitHub issue")

    issue = client.get_issue(ref)
    timestamp = now()
    apply_remote(idea, issue, timestamp)
    idea.updated_at = timestamp
    return _finish(session, idea, timestamp, f"Pulled GitHub issue {issue.ref}: {idea.title}")


@transactional
def upsert_issue(
    session: Session,
    access: AccessContext,
    *,
    repository: str,
    number: int,
    url: str,
    title: str,
    state: str,
    remote_updated_at: datetime,
    body: str = "",
    labels: Sequence[str] = (),
    project_id: str | None = None,
) -> UpsertResult:
    """Creates the task for an issue, or refreshes the task already linked to it."""
    resolved_project_id = resolve_project_id_for_create(
        session, default_project_id_for_create(access, project_id)
    )
    assert_can_access_project(access, resolved_project_id)
    issue = RemoteIssue(
        ref=IssueRef(repository.strip(), number),
        url=url.strip(),
        title=title,
        body=body,
        labels=list(labels),
        state=state,
        updated_at=remote_updated_at,
    )
    timestamp = now()
    existing = session.exec(
        select(IdeaRow).where(
            IdeaRow.project_id == resolved_project_id,
            IdeaRow.github_repository == issue.ref.repository,
            IdeaRow.github_issue_number == issue.ref.number,
        )
    ).first()

    if existing:
        synced = existing.github_issue_updated_at
        if synced is not None and remote_updated_at <= synced:
            return UpsertResult(
                item=decorate_idea(session, existing), created=False, unchanged=True
            )
        apply_remote(existing, issue, timestamp)
        existing.updated_at = timestamp
        item = _finish(session, existing, timestamp, f"Pulled GitHub issue {issue.ref}: {title}")
        return UpsertResult(item=item, created=False, unchanged=False)

    assert_title(title, "Idea")
    idea = IdeaRow(
        id=new_id(),
        project_id=resolved_project_id,
        task_number=next_task_number(session, resolved_project_id),
        title=title.strip(),
        status=IdeaStatus.IDEA,
        labels=[],
        acceptance_criteria=[],
        created_at=timestamp,
        updated_at=timestamp,
    )
    apply_remote(idea, issue, timestamp)
    if idea.details:
        idea.status = IdeaStatus.REFINING
    session.add(idea)
    item = _finish(session, idea, timestamp, f"Imported GitHub issue {issue.ref}: {idea.title}")
    return UpsertResult(item=item, created=True, unchanged=False)
