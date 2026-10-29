"""Open a card's pull request on GitHub, link one opened by hand, and sync it.

The board never pushes: the card's branch must already be on GitHub. The card keeps the PR's
identity and the state it had when it was opened, linked or last synced. A sync only reads
from GitHub: a merged PR moves the card to Done, a PR closed without merging only shows as a
badge, and the card's branch follows a renamed head branch.
"""

import logging
import re
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, admin_access
from vibepod_board.enums import BoardColumn, TaskEventKind
from vibepod_board.errors import BadRequest, BoardError, Conflict, NotFound
from vibepod_board.github import (
    GitHubClient,
    GitHubRateLimited,
    PullRef,
    RemotePull,
    parse_issue_url,
    parse_pull_url,
    repository_from_remote,
)
from vibepod_board.schemas import ApiModel, BoardCard, now
from vibepod_board.services.board import _apply_manual_move, get_card, require_active_card
from vibepod_board.services.common import add_activity, lock, transactional
from vibepod_board.services.history import actor_for, add_task_event
from vibepod_board.tables import BoardCardRow, IdeaRow, TaskDependencyRow

INVALID_URL = "GitHub PR URL must look like https://github.com/owner/repo/pull/123"
# Who the history names for what the periodic sweep finds.
SYNC_ACTOR = "GitHub sync"
# Cards whose open PR the sweep syncs: those waiting for their PR to be opened or merged.
SWEPT_COLUMNS = (BoardColumn.PR_READY, BoardColumn.REVIEW)
# The longest the sweep waits after GitHub rate-limited it, unless GitHub asks for longer.
MAX_BACKOFF_SECONDS = 3600

logger = logging.getLogger(__name__)


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


_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
_QUOTE = re.compile(r" {0,3}>")
_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
_INLINE_CODE = re.compile(r"(`+).+?\1", re.DOTALL)


def _prose(body: str) -> str:
    """The body without the Markdown that GitHub does not read keywords in: fenced code,
    block quotes, HTML comments and inline code."""
    lines = []
    fence = ""
    for line in body.splitlines():
        opening = _FENCE.match(line)
        if fence:
            if opening and opening.group(1)[0] == fence[0] and len(opening.group(1)) >= len(fence):
                if not line.strip().strip(fence[0]):
                    fence = ""
            continue
        if opening:
            fence = opening.group(1)
            continue
        if not _QUOTE.match(line):
            lines.append(line)
    return _INLINE_CODE.sub("", _COMMENT.sub("", "\n".join(lines)))


def _closes(body: str, repository: str, number: int) -> bool:
    """Whether the body already closes issue `number` of `repository` with a closing keyword
    in its prose."""
    name = re.escape(repository)
    target = rf"(?:{name})?#{number}\b|https://github\.com/{name}/issues/{number}\b"
    keyword = r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s+"
    return re.search(f"{keyword}(?:{target})", _prose(body), re.IGNORECASE) is not None


def _body(card: BoardCardRow, idea: IdeaRow | None, repository: str) -> str:
    """The card's suggested description, else the task summary; either closes the linked
    issue when it is in the same repository."""
    body = (card.suggested_pr_body or (idea.summary if idea else "") or "").strip()
    issue = parse_issue_url(card.github_issue_url or "")
    if issue and issue.repository == repository and not _closes(body, repository, issue.number):
        body = f"{body}\n\nCloses #{issue.number}" if body else f"Closes #{issue.number}"
    return body


def _draft(session: Session, card: BoardCardRow, client: GitHubClient) -> PullRequestDraft:
    repository = repository_for(session, card)
    idea = session.get(IdeaRow, card.idea_id) if card.idea_id else None
    stacked = _dependency_branches(session, idea.id, repository, client) if idea else []
    default = client.default_branch(repository)
    bases = [default, *(branch for branch in stacked if branch != default)]
    return PullRequestDraft(
        repository=repository,
        title=card.suggested_pr_title or card.title,
        # A dependency still under review is what this work builds on.
        base=bases[1] if len(bases) > 1 else default,
        bases=bases,
        body=_body(card, idea, repository),
    )


def pull_request_draft(
    session: Session, access: AccessContext, reference: str, client: GitHubClient
) -> PullRequestDraft:
    return _draft(session, require_active_card(session, access, reference), client)


def _store(card: BoardCardRow, pull: RemotePull, synced_at: datetime | None = None) -> None:
    card.github_pr_url = pull.url
    card.github_pr_number = pull.ref.number
    card.github_pr_repository = pull.ref.repository
    card.github_pr_state = pull.state
    card.github_pr_draft = pull.draft
    card.github_pr_base = pull.base
    card.github_pr_title = pull.title
    card.github_pr_body = pull.body
    card.github_pr_head = pull.head
    card.github_pr_head_sha = pull.head_sha
    card.github_pr_merged_at = pull.merged_at
    card.github_pr_synced_at = synced_at or now()


_FIELDS = (
    "url",
    "number",
    "repository",
    "state",
    "draft",
    "base",
    "synced_at",
    "title",
    "body",
    "head",
    "head_sha",
    "merged_at",
    "updated_at",
    "review_decision",
)


def _clear(card: BoardCardRow) -> None:
    for field in _FIELDS:
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
    # Relinking the same PR keeps what the board already made of its state, so a merge that
    # was handled, and a card moved back by hand since, is not handled again.
    same = (card.github_pr_repository, card.github_pr_number) == (ref.repository, ref.number)
    previous_state = card.github_pr_state if same else None
    pull = client.get_pull_request(ref) if client else None
    _clear(card)
    card.github_pr_url = ref.url
    card.github_pr_number = ref.number
    card.github_pr_repository = ref.repository
    linked = _record(session, access, card, TaskEventKind.PR_LINKED, f"PR #{ref.number} linked")
    if client is None or pull is None:
        return linked
    # Linked as a first sync: a PR already merged moves the card to Done, one already closed
    # is recorded, and both after the link in the history.
    card.github_pr_state = previous_state
    _sync(session, card, client, actor_for(session, access), pull, after=linked.updated_at)
    return get_card(session, access, card.id)


def _pulled(card: BoardCardRow) -> tuple[object, ...]:
    """What a sync may change on the card, apart from the time of the sync."""
    fields = tuple(getattr(card, f"github_pr_{field}") for field in _FIELDS if field != "synced_at")
    return (*fields, card.branch_name, card.column_name)


def _sync(
    session: Session,
    card: BoardCardRow,
    client: GitHubClient,
    actor: str,
    pull: RemotePull | None = None,
    *,
    after: datetime | None = None,
) -> None:
    """Applies the PR, fetched from GitHub unless given, to the card, dating what it records
    later than `after`.

    A merge or close is handled when the state differs from the stored one, so it is handled
    once: a card moved back by hand after its PR merged stays where it was put."""
    if not card.github_pr_number or not card.github_pr_repository:
        raise Conflict("Card has no linked pull request")
    ref = PullRef(card.github_pr_repository, card.github_pr_number)
    if pull is None:
        pull = client.get_pull_request(ref)
    before = _pulled(card)
    previous_state = card.github_pr_state
    # A new review changes the PR's `updated_at`, so an unchanged PR costs one request and a
    # changed one two.
    if pull.updated_at is None or pull.updated_at != card.github_pr_updated_at:
        card.github_pr_review_decision = client.get_review_decision(ref)
    timestamp = now()
    if after is not None and timestamp <= after:
        timestamp = after + timedelta(milliseconds=1)
    _store(card, pull, timestamp)
    card.github_pr_updated_at = pull.updated_at

    events: list[tuple[TaskEventKind, str]] = []
    number = ref.number
    if pull.state != previous_state:
        if pull.state == "merged":
            events.append((TaskEventKind.PR_MERGED, f"PR #{number} merged"))
            # Done is what unblocks the tasks that depend on this one.
            if card.column_name != BoardColumn.DONE:
                _apply_manual_move(session, card, BoardColumn.DONE, timestamp, actor)
                card.column_name = BoardColumn.DONE
        elif pull.state == "closed":
            events.append((TaskEventKind.PR_CLOSED, f"PR #{number} closed without merging"))
        elif previous_state is not None:
            events.append((TaskEventKind.PR_REOPENED, f"PR #{number} reopened"))
    # A fork's branch is not one the board can work on.
    if pull.head_repository == ref.repository and pull.head != card.branch_name:
        old = card.branch_name
        message = (
            f"Branch follows PR #{number}: `{old}` renamed to `{pull.head}`"
            if old
            else f"Branch set to `{pull.head}` from PR #{number}"
        )
        events.append((TaskEventKind.BRANCH_RENAMED, message))
        card.branch_name = pull.head

    if _pulled(card) != before:
        card.updated_at = timestamp
    for kind, message in events:
        if card.idea_id:
            add_task_event(session, card.idea_id, kind, message, actor, timestamp)
        add_activity(session, "board.github", f"{message}: {card.title}", timestamp)
    session.flush()


@transactional
def sync_pull_request(
    session: Session, access: AccessContext, reference: str, client: GitHubClient
) -> BoardCard:
    """Pulls the linked PR's state, title and description from GitHub; nothing is written
    back. A merged PR moves the card to Done, and the card's branch follows the PR's head."""
    card = _locked_card(session, access, reference)
    _sync(session, card, client, actor_for(session, access))
    return get_card(session, access, card.id)


def due_pull_requests(session: Session) -> list[str]:
    """Cards the sweep syncs: active, in PR ready or Review, with a PR not known to be closed
    or merged."""
    return list(
        session.exec(
            select(BoardCardRow.id)
            .where(
                col(BoardCardRow.github_pr_number).is_not(None),
                col(BoardCardRow.github_pr_repository).is_not(None),
                col(BoardCardRow.archived_at).is_(None),
                col(BoardCardRow.column_name).in_(list(SWEPT_COLUMNS)),
                (col(BoardCardRow.github_pr_state).is_(None))
                | (BoardCardRow.github_pr_state == "open"),
            )
            .order_by(col(BoardCardRow.github_pr_synced_at).asc().nulls_first())
        ).all()
    )


def sync_due_pull_requests(session: Session, client: GitHubClient) -> int:
    """Syncs every due PR once, each in its own transaction, and says how many were synced.

    A rate limit from GitHub ends the sweep and is raised, so the caller can back off; any
    other failure is logged and the sweep goes on with the next card.
    """
    access = admin_access(SYNC_ACTOR)
    synced = 0
    for card_id in due_pull_requests(session):
        try:
            sync_pull_request(session, access, card_id, client)
        except GitHubRateLimited:
            raise
        except BoardError as error:
            logger.warning("Syncing the pull request of card %s failed: %s", card_id, error)
            continue
        synced += 1
    return synced


def backoff_delay(interval: int, previous: int, retry_after: int | None) -> int:
    """The wait after a rate-limited sweep: twice the previous wait, up to an hour, and never
    shorter than the interval or than GitHub asked for."""
    return max(interval, min(2 * previous, MAX_BACKOFF_SECONDS), retry_after or 0)
