"""Push requests: the board asking a worker to push a card's branch to GitHub.

Workers commit on local branches, and the board has no access to the repositories, so a
branch reaches GitHub only when a worker on the host pushes it with the user's credentials.
The board coordinates that with push requests:

- The approval that completes a task's required approvals asks its reviewer to push the
  reviewed commit (`request_after_approval`, when the project's `push_approved_branches` is
  on). The request starts held by that reviewer, and the verdict reply carries it.
- Open PR on a card whose branch is missing on GitHub asks any worker of the project
  (`request_push` with the pull request to open). Workers see pending requests in their
  heartbeat reply (`pending_for_worker`), claim one and push.

A request is leased like a claim (`PUSH_LEASE`): one that is not reported in time goes back
to pending, unassigned, for any worker. The lapse is swept on read, as claims and reviews are
(`_sweep`). The holder reports the outcome (`report_result`): a push records the commit on
the card (`branch_pushed_sha` / `branch_pushed_at`) and, when the request carries a pull
request, the board opens it; a failure keeps the error and ends the request. At most one
request per card is pending or claimed at a time.

Writes lock the task, the card and then the request, the order every other card write takes.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlmodel import Session, col, select

from vibepod_board.access import AccessContext, assert_can_access_project
from vibepod_board.enums import BoardColumn, PushReason, PushStatus, TaskEventKind
from vibepod_board.errors import BadRequest, BoardError, Conflict, NotFound
from vibepod_board.github import GitHubClient
from vibepod_board.schemas import PushRequest, now
from vibepod_board.services.board import require_active_card
from vibepod_board.services.claims import _holder, normalize_sha
from vibepod_board.services.common import add_activity, lock, new_id, transactional
from vibepod_board.services.history import actor_for, add_task_event
from vibepod_board.services.pull_requests import open_pull_request, repository_for
from vibepod_board.services.references import task_keys
from vibepod_board.tables import BoardCardRow, IdeaRow, ProjectRow, PushRequestRow

PUSH_LEASE = timedelta(minutes=5)
# Pending requests a heartbeat reply carries at most.
HEARTBEAT_LIMIT = 5
ACTIVE = (PushStatus.PENDING, PushStatus.CLAIMED)
FINISHED = (PushStatus.PUSHED, PushStatus.FAILED)
ERROR_LIMIT = 2000
OPEN_PR_FIELDS = ("title", "body", "base", "draft")


def _short(sha: str | None) -> str:
    return sha[:12] if sha else "the branch tip"


def request_from_row(row: PushRequestRow, key: str | None = None) -> PushRequest:
    return PushRequest(
        id=row.id,
        card_id=row.card_id,
        task_key=key,
        branch=row.branch,
        head_sha=row.head_sha,
        repository_local_path=row.repository_local_path,
        repository_remote_url=row.repository_remote_url,
        repository=row.repository,
        reason=PushReason(row.reason),
        status=PushStatus(row.status),
        assignee=row.assignee,
        lease_expires_at=row.lease_expires_at,
        pushed_sha=row.pushed_sha,
        error=row.error,
        pull_request_url=row.pull_request_url,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _requests_out(session: Session, rows: list[PushRequestRow]) -> list[PushRequest]:
    session.flush()
    cards = {
        card.id: card.idea_id
        for card in session.exec(
            select(BoardCardRow).where(col(BoardCardRow.id).in_({row.card_id for row in rows}))
        ).all()
    }
    keys = task_keys(session, [idea for idea in cards.values() if idea])
    return [request_from_row(row, keys.get(cards.get(row.card_id) or "")) for row in rows]


def _out(session: Session, row: PushRequestRow) -> PushRequest:
    return _requests_out(session, [row])[0]


def _sweep(
    session: Session,
    timestamp: datetime,
    *,
    project_id: str | None = None,
    card_id: str | None = None,
) -> None:
    """Puts requests whose lease ran out back to pending with no holder. Rows another
    transaction holds are left for a later read, so the sweep never waits on a write."""
    query = select(PushRequestRow).where(
        col(PushRequestRow.status).in_(list(ACTIVE)),
        col(PushRequestRow.lease_expires_at) <= timestamp,
    )
    if project_id is not None:
        query = query.where(PushRequestRow.project_id == project_id)
    if card_id is not None:
        query = query.where(PushRequestRow.card_id == card_id)
    rows = session.exec(
        query.with_for_update(skip_locked=True).execution_options(populate_existing=True)
    ).all()
    for row in rows:
        _lapse(row, timestamp)
    if rows:
        session.flush()


def _lapse(row: PushRequestRow, timestamp: datetime) -> None:
    row.status = PushStatus.PENDING
    row.assignee = None
    row.lease_expires_at = None
    row.updated_at = timestamp


def _active_of(session: Session, card_id: str) -> PushRequestRow | None:
    return session.exec(
        select(PushRequestRow)
        .where(
            PushRequestRow.card_id == card_id,
            col(PushRequestRow.status).in_(list(ACTIVE)),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _repository(session: Session, card: BoardCardRow) -> str | None:
    try:
        return repository_for(session, card)
    except BadRequest:
        return None


def _open_pr_options(open_pr: dict[str, Any] | None) -> dict[str, Any] | None:
    if open_pr is None:
        return None
    options = {field: open_pr.get(field) for field in OPEN_PR_FIELDS}
    for field in ("title", "base"):
        if options[field] is not None and not str(options[field]).strip():
            raise BadRequest(f"Pull request {field} is required")
    options["draft"] = bool(options["draft"])
    return options


def _new_request(
    session: Session,
    card: BoardCardRow,
    reason: PushReason,
    actor: str,
    timestamp: datetime,
    *,
    head_sha: str | None,
    open_pr: dict[str, Any] | None = None,
    holder: str | None = None,
) -> PushRequestRow:
    assert card.branch_name
    row = PushRequestRow(
        id=new_id(),
        card_id=card.id,
        project_id=card.project_id,
        branch=card.branch_name,
        head_sha=head_sha,
        repository_local_path=card.repository_local_path,
        repository_remote_url=card.repository_remote_url,
        repository=_repository(session, card),
        reason=reason,
        status=PushStatus.CLAIMED if holder else PushStatus.PENDING,
        assignee=holder,
        lease_expires_at=timestamp + PUSH_LEASE if holder else None,
        open_pr=open_pr,
        requested_by=actor,
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(row)
    if card.idea_id:
        who = f" of {holder}" if holder else ""
        add_task_event(
            session,
            card.idea_id,
            TaskEventKind.PUSH_REQUESTED,
            f"Push of {card.branch_name} at {_short(head_sha)} requested{who} by {actor}",
            actor,
            timestamp,
        )
    session.flush()
    return row


def _move_to_head(row: PushRequestRow, card: BoardCardRow, timestamp: datetime) -> bool:
    """Points a pending request nobody holds at the card's head commit, which moved on since
    it was asked for (a later hand-over). A request a worker holds is left alone: it may be
    pushing already. Says whether the request now is for the head."""
    if row.status != PushStatus.PENDING or row.assignee:
        return row.head_sha == card.head_sha
    if row.head_sha != card.head_sha:
        row.head_sha = card.head_sha
        row.updated_at = timestamp
    return True


def _locked_card(session: Session, access: AccessContext, reference: str) -> BoardCardRow:
    found = require_active_card(session, access, reference)
    # Task before card, the order every write takes.
    if found.idea_id:
        lock(session, IdeaRow, found.idea_id)
    return lock(session, BoardCardRow, found.id)


@transactional
def request_push(
    session: Session,
    access: AccessContext,
    reference: str,
    open_pr: dict[str, Any] | None = None,
) -> PushRequest:
    """Asks the project's workers to push the card's branch at its handed-over head commit,
    not whatever the worker's local branch points to; only a card without a known head asks
    for the branch tip. With `open_pr` (title, body, base, draft; each optional) the board
    opens the pull request once the branch is pushed, which needs the card in PR ready.

    A request already pending or claimed for the card is returned instead of a new one; the
    pull request options of this call are added to it. A pending one asked for before the
    card's latest hand-over is moved to its head; one a worker holds for an older commit is
    refused until that push is reported."""
    card = _locked_card(session, access, reference)
    if not card.branch_name:
        raise Conflict("Set the card's branch before asking for a push")
    options = _open_pr_options(open_pr)
    if options is not None:
        if card.column_name != BoardColumn.PR_READY:
            raise Conflict("Only cards in PR ready can open a pull request")
        if card.github_pr_number and card.github_pr_state != "closed":
            raise Conflict(f"Card already has PR #{card.github_pr_number}; unlink it first")
    timestamp = now()
    _sweep(session, timestamp, card_id=card.id)
    existing = _active_of(session, card.id)
    if existing is not None and existing.head_sha != card.head_sha:
        if not _move_to_head(existing, card, timestamp):
            raise Conflict(
                f"{existing.assignee} is pushing {existing.branch} at "
                f"{_short(existing.head_sha)}, not the card's head {_short(card.head_sha)}; "
                "ask again once that push is reported"
            )
    if existing is not None:
        if options is not None:
            existing.open_pr = options
            existing.updated_at = timestamp
        return _out(session, existing)
    actor = actor_for(session, access)
    row = _new_request(
        session, card, PushReason.OPEN_PR, actor, timestamp, head_sha=card.head_sha, open_pr=options
    )
    add_activity(
        session, "board.push_requested", f"{actor} asked to push {card.branch_name}", timestamp
    )
    return _out(session, row)


def request_after_approval(
    session: Session,
    project: ProjectRow,
    card: BoardCardRow,
    reviewer: str,
    head_sha: str | None,
    timestamp: datetime,
) -> PushRequestRow | None:
    """The request the approval that moved the card to PR ready makes, held by its reviewer
    for the reviewed commit; inside the verdict's transaction, with task and card locked. A
    request still open from earlier is superseded: it is for an older commit."""
    if not project.push_approved_branches or not card.branch_name:
        return None
    _sweep(session, timestamp, card_id=card.id)
    earlier = _active_of(session, card.id)
    if earlier is not None:
        earlier.status = PushStatus.FAILED
        earlier.error = f"Superseded by the push {reviewer} was asked for at approval"
        earlier.assignee = None
        earlier.lease_expires_at = None
        earlier.updated_at = timestamp
        session.flush()
    return _new_request(
        session, card, PushReason.APPROVED, reviewer, timestamp, head_sha=head_sha, holder=reviewer
    )


def push_request_out(session: Session, row: PushRequestRow) -> PushRequest:
    return _out(session, row)


@transactional
def latest_push_request(session: Session, access: AccessContext, reference: str) -> PushRequest:
    """The card's latest push request; a lapsed lease is swept first."""
    card = require_active_card(session, access, reference)
    _sweep(session, now(), card_id=card.id)
    row = session.exec(
        select(PushRequestRow)
        .where(PushRequestRow.card_id == card.id)
        .order_by(col(PushRequestRow.created_at).desc(), col(PushRequestRow.id).desc())
        .limit(1)
    ).first()
    if row is None:
        raise NotFound("No push request for this card")
    return _out(session, row)


def _require_request(session: Session, access: AccessContext, request_id: str) -> PushRequestRow:
    row = session.get(PushRequestRow, request_id.strip())
    if row is None:
        raise NotFound(f"Push request not found: {request_id}")
    try:
        assert_can_access_project(access, row.project_id)
    except BoardError:
        raise NotFound(f"Push request not found: {request_id}") from None
    return row


def _locked_request(session: Session, access: AccessContext, request_id: str) -> PushRequestRow:
    """The request with its task, card and itself locked, in that order."""
    found = _require_request(session, access, request_id)
    card = session.get(BoardCardRow, found.card_id)
    if card is not None and card.idea_id:
        lock(session, IdeaRow, card.idea_id)
    if card is not None:
        lock(session, BoardCardRow, card.id)
    return session.exec(
        select(PushRequestRow)
        .where(PushRequestRow.id == found.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


@transactional
def claim_push_request(
    session: Session, access: AccessContext, request_id: str, assignee: str
) -> PushRequest:
    """Takes a pending request with a 5 minute lease; the holder claiming again renews it. A
    request held by someone else, such as the reviewer it was made for, is refused until its
    lease runs out, and so is a finished one."""
    holder = _holder(assignee)
    row = _locked_request(session, access, request_id)
    timestamp = now()
    if row.status in ACTIVE and row.lease_expires_at and row.lease_expires_at <= timestamp:
        _lapse(row, timestamp)
    if row.status in FINISHED:
        raise Conflict(f"Push request {row.id} already ended: {row.status}")
    if row.assignee and row.assignee != holder:
        until = row.lease_expires_at.isoformat() if row.lease_expires_at else "it ends"
        raise Conflict(f"Push request {row.id} is held by {row.assignee} until {until}")
    row.status = PushStatus.CLAIMED
    row.assignee = holder
    row.lease_expires_at = timestamp + PUSH_LEASE
    row.updated_at = timestamp
    return _out(session, row)


@transactional
def _record_result(
    session: Session,
    access: AccessContext,
    request_id: str,
    holder: str,
    status: PushStatus,
    pushed_sha: str | None,
    error: str | None,
) -> PushRequestRow:
    row = _locked_request(session, access, request_id)
    if row.status != PushStatus.CLAIMED or row.assignee != holder:
        shown = f"held by {row.assignee}" if row.assignee else str(row.status)
        raise Conflict(f"Push request {row.id} is not held by {holder}: it is {shown}")
    timestamp = now()
    card = session.get(BoardCardRow, row.card_id)
    row.status = status
    row.lease_expires_at = None
    row.updated_at = timestamp
    if status == PushStatus.PUSHED:
        row.pushed_sha = pushed_sha or row.head_sha
        row.error = None
        message = f"{row.branch} pushed at {_short(row.pushed_sha)} by {holder}"
        kind = TaskEventKind.BRANCH_PUSHED
        if card is not None:
            card.branch_pushed_sha = row.pushed_sha
            card.branch_pushed_at = timestamp
            card.updated_at = timestamp
    else:
        row.error = (error or "").strip()[:ERROR_LIMIT] or "The push failed"
        message = f"Push of {row.branch} by {holder} failed: {row.error}"
        kind = TaskEventKind.PUSH_FAILED
    if card is not None and card.idea_id:
        add_task_event(session, card.idea_id, kind, message, holder, timestamp)
    add_activity(session, f"board.{kind}", message, timestamp)
    session.flush()
    return row


@transactional
def _record_pull_request(
    session: Session, request_id: str, url: str | None, error: str | None
) -> PushRequestRow:
    row = session.exec(
        select(PushRequestRow)
        .where(PushRequestRow.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    row.pull_request_url = url
    row.error = error[:ERROR_LIMIT] if error else None
    row.updated_at = now()
    session.flush()
    return row


def report_result(
    session: Session,
    access: AccessContext,
    request_id: str,
    assignee: str,
    status: PushStatus,
    pushed_sha: str | None = None,
    error: str | None = None,
    client: GitHubClient | None = None,
) -> PushRequest:
    """The holder reports how the push went; it ends the request. A push records the commit
    on the card, and when the request carries a pull request the board opens it then: its
    link lands in `pullRequestUrl`, or the reason it could not open in `error` while the
    request stays pushed. Without a GitHub client the pull request cannot open."""
    holder = _holder(assignee)
    status = PushStatus(status)
    if status not in FINISHED:
        raise BadRequest("status must be pushed or failed")
    sha = normalize_sha(pushed_sha)
    row = _record_result(session, access, request_id, holder, status, sha, error)
    if status != PushStatus.PUSHED or row.open_pr is None:
        return _out(session, row)
    options = row.open_pr
    try:
        if client is None:
            raise BadRequest("GitHub is disabled: set GITHUB_TOKEN to open the pull request")
        card = open_pull_request(
            session,
            access,
            row.card_id,
            client,
            title=options.get("title"),
            base=options.get("base"),
            body=options.get("body"),
            draft=bool(options.get("draft")),
        )
        url, failure = card.github_pr_url, None
    except BoardError as failed:
        url, failure = None, f"The branch was pushed, but the pull request failed: {failed}"
    return _out(session, _record_pull_request(session, row.id, url, failure))


def pending_for_worker(
    session: Session, project_id: str, worker: str, timestamp: datetime | None = None
) -> list[PushRequest]:
    """What a worker's heartbeat reply offers: pending requests of its project nobody else
    holds, oldest first. Inside the heartbeat's transaction; lapsed leases are swept first."""
    timestamp = timestamp or now()
    _sweep(session, timestamp, project_id=project_id)
    rows = session.exec(
        select(PushRequestRow)
        .where(
            PushRequestRow.project_id == project_id,
            PushRequestRow.status == PushStatus.PENDING,
            or_(col(PushRequestRow.assignee).is_(None), PushRequestRow.assignee == worker),
        )
        .order_by(col(PushRequestRow.created_at), col(PushRequestRow.id))
        .limit(HEARTBEAT_LIMIT)
    ).all()
    return _requests_out(session, list(rows)) if rows else []
