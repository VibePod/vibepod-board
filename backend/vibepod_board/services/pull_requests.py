"""Open a card's pull request on GitHub, or link one opened by hand.

The board never pushes: the card's branch must already be on GitHub. The card keeps the PR's
identity and the state it had when it was opened or linked; keeping that state in sync is
not done here.
"""

from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext
from vibepod_board.enums import BoardColumn, TaskEventKind
from vibepod_board.errors import BadRequest, Conflict, NotFound
from vibepod_board.github import (
    GitHubClient,
    PullRef,
    RemotePull,
    parse_issue_url,
    parse_pull_url,
    repository_from_remote,
)
from vibepod_board.schemas import ApiModel, BoardCard, now
from vibepod_board.services.board import get_card, require_active_card
from vibepod_board.services.common import add_activity, lock, transactional
from vibepod_board.services.history import actor_for, add_task_event
from vibepod_board.tables import BoardCardRow, IdeaRow, TaskDependencyRow, TaskEventRow, TaskRunRow

INVALID_URL = "GitHub PR URL must look like https://github.com/owner/repo/pull/123"


class PullRequestDraft(ApiModel):
    """What the Open PR dialog starts from."""

    repository: str
    title: str
    base: str
    # The default branch, then the branches of dependencies' open PRs for stacked work.
    bases: list[str]
    body: str
    draft: bool = False


def repository_for(session: Session, card: BoardCardRow) -> str:
    """The task's repository: from its remote URL, else from its linked issue."""
    idea = session.get(IdeaRow, card.idea_id) if card.idea_id else None
    issue = parse_issue_url(card.github_issue_url or "")
    repository = (
        repository_from_remote(card.repository_remote_url)
        or (idea.github_repository if idea else None)
        or (issue.repository if issue else None)
    )
    if not repository:
        raise BadRequest("No GitHub repository for this task: set its remote URL or link an issue")
    return repository


def _dependency_branches(
    session: Session, idea_id: str, repository: str, client: GitHubClient
) -> list[str]:
    """Head branches of the open PRs of the task's dependencies in the same repository.

    The stored state may be old, so each PR is asked for again.
    """
    rows = session.exec(
        select(BoardCardRow.github_pr_number)
        .join(TaskDependencyRow, col(TaskDependencyRow.depends_on_idea_id) == BoardCardRow.idea_id)
        .where(
            TaskDependencyRow.idea_id == idea_id,
            BoardCardRow.github_pr_repository == repository,
            col(BoardCardRow.github_pr_number).is_not(None),
        )
    ).all()
    branches = []
    for number in sorted(set(rows)):
        try:
            pull = client.get_pull_request(PullRef(repository, number))
        except NotFound:
            continue
        # A PR from a fork has its head in another repository: that branch is no base here.
        if pull.head_repository != repository:
            continue
        if pull.state == "open" and pull.head not in branches:
            branches.append(pull.head)
    return branches


def _body(session: Session, card: BoardCardRow, idea: IdeaRow | None, repository: str) -> str:
    sections = []
    summary = idea.summary if idea else ""
    if summary or card.details:
        sections.append(f"## Summary\n{summary or card.details}")
    if idea and idea.acceptance_criteria:
        criteria = "\n".join(f"- [ ] {item}" for item in idea.acceptance_criteria)
        sections.append(f"## Acceptance Criteria\n{criteria}")
    issue = parse_issue_url(card.github_issue_url or "")
    if issue and issue.repository == repository:
        sections.append(f"Closes #{issue.number}")
    if idea:
        approvals = session.exec(
            select(TaskEventRow.message)
            .where(TaskEventRow.idea_id == idea.id, TaskEventRow.kind == TaskEventKind.APPROVED)
            .order_by(col(TaskEventRow.created_at))
        ).all()
        if approvals:
            sections.append("## Review\n" + "\n".join(f"- {message}" for message in approvals))
        run = session.exec(
            select(TaskRunRow)
            .where(TaskRunRow.idea_id == idea.id)
            .order_by(col(TaskRunRow.created_at).desc(), col(TaskRunRow.id).desc())
        ).first()
        if run and run.summary.strip():
            sections.append(f"## Latest run\n{run.summary.strip()}")
    return "\n\n".join(sections)


def _draft(session: Session, card: BoardCardRow, client: GitHubClient) -> PullRequestDraft:
    repository = repository_for(session, card)
    idea = session.get(IdeaRow, card.idea_id) if card.idea_id else None
    stacked = _dependency_branches(session, idea.id, repository, client) if idea else []
    default = client.default_branch(repository)
    bases = [default, *(branch for branch in stacked if branch != default)]
    return PullRequestDraft(
        repository=repository,
        title=card.title,
        # A dependency still under review is what this work builds on.
        base=bases[1] if len(bases) > 1 else default,
        bases=bases,
        body=_body(session, card, idea, repository),
    )


def pull_request_draft(
    session: Session, access: AccessContext, reference: str, client: GitHubClient
) -> PullRequestDraft:
    return _draft(session, require_active_card(session, access, reference), client)


def _store(card: BoardCardRow, pull: RemotePull) -> None:
    card.github_pr_url = pull.url
    card.github_pr_number = pull.ref.number
    card.github_pr_repository = pull.ref.repository
    card.github_pr_state = pull.state
    card.github_pr_draft = pull.draft
    card.github_pr_base = pull.base
    card.github_pr_synced_at = now()


def _clear(card: BoardCardRow) -> None:
    for field in ("url", "number", "repository", "state", "draft", "base", "synced_at"):
        setattr(card, f"github_pr_{field}", None)


def _record(
    session: Session, access: AccessContext, card: BoardCardRow, kind: TaskEventKind, message: str
) -> BoardCard:
    actor = actor_for(session, access)
    timestamp = now()
    card.updated_at = timestamp
    if card.idea_id:
        add_task_event(session, card.idea_id, kind, f"{message} by {actor}", actor, timestamp)
    add_activity(session, "board.github", f"{message}: {card.title}", timestamp)
    session.flush()
    return get_card(session, access, card.id)


def _locked_card(session: Session, access: AccessContext, reference: str) -> BoardCardRow:
    found = require_active_card(session, access, reference)
    # Task before card, the order every write takes.
    if found.idea_id:
        lock(session, IdeaRow, found.idea_id)
    return lock(session, BoardCardRow, found.id)


@transactional
def open_pull_request(
    session: Session,
    access: AccessContext,
    reference: str,
    client: GitHubClient,
    *,
    title: str | None = None,
    base: str | None = None,
    body: str | None = None,
    draft: bool = False,
) -> BoardCard:
    """Opens the PR for the card's branch; an open PR that already exists is linked instead.

    Fields left out take the values the dialog is prefilled with.
    """
    card = _locked_card(session, access, reference)
    if card.column_name != BoardColumn.PR_READY:
        raise Conflict("Only cards in PR ready can open a pull request")
    if not card.branch_name:
        raise BadRequest("Set the card's branch before opening a pull request")
    if card.github_pr_number and card.github_pr_state != "closed":
        raise Conflict(f"Card already has PR #{card.github_pr_number}; unlink it first")
    if title is not None and not title.strip():
        raise BadRequest("Pull request title is required")
    if base is not None and not base.strip():
        raise BadRequest("Pull request base is required")
    repository = repository_for(session, card)
    if not client.branch_exists(repository, card.branch_name):
        raise BadRequest(f"Push branch `{card.branch_name}` to GitHub first")
    prefill = _draft(session, card, client) if None in (title, base, body) else None
    pull, created = client.create_pull_request(
        repository,
        head=card.branch_name,
        base=(base if base is not None else prefill.base).strip(),
        title=(title if title is not None else prefill.title).strip(),
        body=body if body is not None else prefill.body,
        draft=draft,
    )
    _store(card, pull)
    number = pull.ref.number
    if created:
        return _record(session, access, card, TaskEventKind.PR_OPENED, f"PR #{number} opened")
    return _record(session, access, card, TaskEventKind.PR_LINKED, f"PR #{number} linked")


@transactional
def link_pull_request(
    session: Session,
    access: AccessContext,
    reference: str,
    url: str,
    client: GitHubClient | None = None,
) -> BoardCard:
    """Links a PR opened by hand, or unlinks with an empty URL.

    With a client the PR's state is read from GitHub; without one only its identity is kept.
    """
    card = _locked_card(session, access, reference)
    if not url.strip():
        number = card.github_pr_number
        if number is None:
            return get_card(session, access, card.id)
        _clear(card)
        return _record(session, access, card, TaskEventKind.PR_UNLINKED, f"PR #{number} unlinked")
    ref = parse_pull_url(url)
    if ref is None:
        raise BadRequest(INVALID_URL)
    _clear(card)
    if client:
        _store(card, client.get_pull_request(ref))
    else:
        card.github_pr_url = ref.url
        card.github_pr_number = ref.number
        card.github_pr_repository = ref.repository
    return _record(session, access, card, TaskEventKind.PR_LINKED, f"PR #{ref.number} linked")
