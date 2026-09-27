"""Linking a task to a GitHub issue, shared by manual linking, push, pull and import."""

from datetime import datetime

from sqlmodel import Session, select

from vibepod_board.errors import BadRequest, Conflict
from vibepod_board.github import IssueRef, RemoteIssue, parse_issue_url
from vibepod_board.graph import format_task_key
from vibepod_board.tables import IdeaRow, ProjectRow

INVALID_URL = "GitHub issue URL must look like https://github.com/owner/repo/issues/123"


def assert_issue_available(session: Session, idea: IdeaRow, ref: IssueRef) -> None:
    """One issue maps to at most one task per project."""
    other = session.exec(
        select(IdeaRow).where(
            IdeaRow.project_id == idea.project_id,
            IdeaRow.github_repository == ref.repository,
            IdeaRow.github_issue_number == ref.number,
            IdeaRow.id != idea.id,
        )
    ).first()
    if other:
        project = session.get(ProjectRow, other.project_id)
        task_key = format_task_key(project.key if project else "", other.task_number)
        raise Conflict(f"GitHub issue {ref} is already linked to task {task_key}")


def unlink(idea: IdeaRow) -> None:
    idea.github_issue_url = None
    idea.github_issue_number = None
    idea.github_repository = None
    idea.github_issue_state = None
    idea.github_issue_updated_at = None
    idea.github_synced_at = None


def link_url(session: Session, idea: IdeaRow, url: str) -> None:
    """Links the task by issue URL; an empty URL unlinks it."""
    if not url.strip():
        unlink(idea)
        return
    ref = parse_issue_url(url)
    if ref is None:
        raise BadRequest(INVALID_URL)
    if (idea.github_repository, idea.github_issue_number) == (ref.repository, ref.number):
        return
    assert_issue_available(session, idea, ref)
    unlink(idea)
    idea.github_repository = ref.repository
    idea.github_issue_number = ref.number
    idea.github_issue_url = ref.url


def apply_remote(
    idea: IdeaRow, issue: RemoteIssue, synced_at: datetime, *, content: bool = True
) -> None:
    """Stores the remote identity and sync state; with `content`, also title and labels.
    Details are never overwritten once written, since they hold local refinement."""
    idea.github_repository = issue.ref.repository
    idea.github_issue_number = issue.ref.number
    idea.github_issue_url = issue.url
    idea.github_issue_state = issue.state
    idea.github_issue_updated_at = issue.updated_at
    idea.github_synced_at = synced_at
    if content:
        idea.title = issue.title.strip() or idea.title
        idea.labels = list(dict.fromkeys(label.strip() for label in issue.labels if label.strip()))
        if not idea.details and issue.body.strip():
            idea.details = issue.body.strip()
