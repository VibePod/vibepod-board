"""Minimal GitHub REST client for issue sync and pull requests, plus URL helpers.

The token comes from `GITHUB_TOKEN`; it is never stored in the database. Tests pass an
`httpx.MockTransport`, so no test talks to GitHub.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from vibepod_board.errors import BoardError, NotFound

API_URL = "https://api.github.com"
_ISSUE_URL = re.compile(
    r"(?i)^https://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(?P<number>\d+)/?$"
)
_PULL_URL = re.compile(
    r"(?i)^https://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(?P<number>\d+)/?$"
)
_REPO_PATH = re.compile(r"^(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


class GitHubUnavailable(BoardError):
    """GitHub refused or failed the request (bad gateway from the board's point of view)."""

    status_code = 502


class GitHubRateLimited(GitHubUnavailable):
    """GitHub answered 429, or 403 for a spent rate limit; `retry_after` is in seconds when
    GitHub said how long to wait."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class IssueRef:
    repository: str
    number: int

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repository}/issues/{self.number}"

    def __str__(self) -> str:
        return f"{self.repository}#{self.number}"


@dataclass(frozen=True)
class PullRef:
    repository: str
    number: int

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repository}/pull/{self.number}"

    def __str__(self) -> str:
        return f"{self.repository}#{self.number}"


@dataclass(frozen=True)
class RemotePull:
    ref: PullRef
    url: str
    # open, closed or merged: GitHub reports a merged PR as closed with `merged_at` set.
    state: str
    draft: bool
    head: str
    base: str
    # The repository the head branch lives in: another one for a PR from a fork, None when
    # that fork was deleted.
    head_repository: str | None = None
    title: str = ""
    body: str = ""
    head_sha: str | None = None
    merged_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class RemoteIssue:
    ref: IssueRef
    url: str
    title: str
    body: str
    labels: list[str]
    state: str
    updated_at: datetime


def normalize_repository(repository: str) -> str:
    """GitHub treats `owner/repo` case-insensitively, so identities are stored lowercase."""
    return repository.strip().lower()


def parse_issue_url(url: str) -> IssueRef | None:
    match = _ISSUE_URL.match(url.strip())
    if not match:
        return None
    return IssueRef(normalize_repository(match["repo"]), int(match["number"]))


def parse_pull_url(url: str) -> PullRef | None:
    match = _PULL_URL.match(url.strip())
    if not match:
        return None
    return PullRef(normalize_repository(match["repo"]), int(match["number"]))


def repository_from_remote(remote: str | None) -> str | None:
    """`owner/repo` from an https or scp-style GitHub remote, else None."""
    if not remote:
        return None
    remote = remote.strip()
    for prefix in ("git@github.com:", "https://github.com/", "ssh://git@github.com/"):
        if remote.startswith(prefix):
            match = _REPO_PATH.match(remote[len(prefix) :])
            return normalize_repository(match["repo"]) if match else None
    return None


def issue_body(summary: str, details: str, acceptance_criteria: list[str]) -> str:
    sections = []
    if summary:
        sections.append(f"## Summary\n{summary}")
    if details:
        sections.append(f"## Details\n{details}")
    if acceptance_criteria:
        criteria = "\n".join(f"- {item}" for item in acceptance_criteria)
        sections.append(f"## Acceptance Criteria\n{criteria}")
    return "\n\n".join(sections) or "Created from vibepod-board."


def _timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _remote_issue(repository: str, data: dict[str, Any]) -> RemoteIssue:
    return RemoteIssue(
        ref=IssueRef(normalize_repository(repository), int(data["number"])),
        url=data["html_url"],
        title=data["title"],
        body=data.get("body") or "",
        labels=[label["name"] if isinstance(label, dict) else label for label in data["labels"]],
        state=data["state"],
        updated_at=_timestamp(data["updated_at"]),
    )


def _remote_pull(repository: str, data: dict[str, Any]) -> RemotePull:
    return RemotePull(
        ref=PullRef(normalize_repository(repository), int(data["number"])),
        url=data["html_url"],
        state="merged" if data.get("merged_at") else data["state"],
        draft=bool(data.get("draft")),
        head=data["head"]["ref"],
        base=data["base"]["ref"],
        head_repository=_head_repository(data["head"]),
        title=data.get("title") or "",
        body=data.get("body") or "",
        head_sha=data["head"].get("sha"),
        merged_at=_timestamp(data.get("merged_at")),
        updated_at=_timestamp(data.get("updated_at")),
    )


def review_decision(reviews: list[dict[str, Any]]) -> str | None:
    """approved, changes_requested or None from a PR's reviews, oldest first.

    Each reviewer counts with their latest approval, change request or dismissal; comments do
    not change where they stand. One change request outweighs any approvals, as on GitHub.
    """
    latest: dict[str, str] = {}
    for review in reviews:
        user = review.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        state = review.get("state")
        if login and state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[login] = state
    if "CHANGES_REQUESTED" in latest.values():
        return "changes_requested"
    if "APPROVED" in latest.values():
        return "approved"
    return None


def _retry_after(response: httpx.Response) -> int | None:
    """Seconds to wait from `Retry-After`, else until `X-RateLimit-Reset`."""
    retry_after = response.headers.get("retry-after", "")
    if retry_after.isdigit():
        return int(retry_after)
    reset = response.headers.get("x-ratelimit-reset", "")
    if reset.isdigit():
        return max(0, int(reset) - int(datetime.now().timestamp()))
    return None


def _rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    # A spent primary limit says so in its headers; a secondary limit sends Retry-After.
    return response.status_code == 403 and (
        response.headers.get("x-ratelimit-remaining") == "0"
        or "retry-after" in response.headers
        or "rate limit" in response.text.lower()
    )


def _head_repository(head: dict[str, Any]) -> str | None:
    repo = head.get("repo")
    name = repo.get("full_name") if isinstance(repo, dict) else None
    return normalize_repository(name) if name else None


def _error_detail(response: httpx.Response, limit: int = 300) -> str:
    """GitHub's own explanation (e.g. why a 422 failed), bounded for error messages."""
    try:
        payload = response.json()
        detail = payload.get("message") if isinstance(payload, dict) else None
        # A 422 says "Validation Failed" and puts the reason, such as a missing base
        # branch, in `errors`.
        reasons = [
            error["message"]
            for error in (payload.get("errors") or [] if isinstance(payload, dict) else [])
            if isinstance(error, dict) and error.get("message")
        ]
        if detail and reasons:
            detail = f"{detail}: {'; '.join(reasons)}"
    except ValueError:
        detail = None
    return (detail or response.text or "")[:limit]


class GitHubClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=API_URL,
            transport=transport,
            timeout=30,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "vibepod-board",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise GitHubUnavailable(f"GitHub request failed: {error}") from error
        if _rate_limited(response):
            retry_after = _retry_after(response)
            wait = f"; retry in {retry_after}s" if retry_after is not None else ""
            raise GitHubRateLimited(f"GitHub rate limit reached{wait}", retry_after)
        if response.status_code in (401, 403):
            raise GitHubUnavailable("GitHub rejected the token")
        return response

    def _request(self, method: str, path: str, missing: str, **kwargs: Any) -> Any:
        return self._json(self._send(method, path, **kwargs), missing)

    @staticmethod
    def _json(response: httpx.Response, missing: str) -> Any:
        """The JSON answer; a 404 raises NotFound with the `missing` message."""
        if response.status_code == 404:
            raise NotFound(missing)
        if response.is_error:
            raise GitHubUnavailable(
                f"GitHub request failed: {response.status_code} {_error_detail(response)}".strip()
            )
        return response.json()

    def get_issue(self, ref: IssueRef) -> RemoteIssue:
        data = self._request(
            "GET",
            f"/repos/{ref.repository}/issues/{ref.number}",
            f"Issue not found on GitHub: {ref}",
        )
        return _remote_issue(ref.repository, data)

    def create_issue(
        self, repository: str, title: str, body: str, labels: list[str]
    ) -> RemoteIssue:
        data = self._request(
            "POST",
            f"/repos/{repository}/issues",
            f"Repository not found on GitHub: {repository}",
            json={"title": title, "body": body, "labels": labels},
        )
        return _remote_issue(repository, data)

    def update_issue(self, ref: IssueRef, title: str, body: str, labels: list[str]) -> RemoteIssue:
        data = self._request(
            "PATCH",
            f"/repos/{ref.repository}/issues/{ref.number}",
            f"Issue not found on GitHub: {ref}",
            json={"title": title, "body": body, "labels": labels},
        )
        return _remote_issue(ref.repository, data)

    def default_branch(self, repository: str) -> str:
        data = self._request(
            "GET", f"/repos/{repository}", f"Repository not found on GitHub: {repository}"
        )
        return data["default_branch"]

    def branch_exists(self, repository: str, branch: str) -> bool:
        response = self._send("GET", f"/repos/{repository}/branches/{quote(branch, safe='')}")
        if response.status_code == 404:
            return False
        self._json(response, f"Branch not found on GitHub: {branch}")
        return True

    def get_pull_request(self, ref: PullRef) -> RemotePull:
        data = self._request(
            "GET",
            f"/repos/{ref.repository}/pulls/{ref.number}",
            f"Pull request not found on GitHub: {ref}",
        )
        return _remote_pull(ref.repository, data)

    def get_review_decision(self, ref: PullRef) -> str | None:
        """approved, changes_requested or None, from the PR's reviews."""
        reviews = self._request(
            "GET",
            f"/repos/{ref.repository}/pulls/{ref.number}/reviews",
            f"Pull request not found on GitHub: {ref}",
            params={"per_page": 100},
        )
        return review_decision(reviews)

    def create_pull_request(
        self, repository: str, head: str, base: str, title: str, body: str, draft: bool
    ) -> tuple[RemotePull, bool]:
        """The new PR and True, or the open PR that already exists for `head` and False."""
        response = self._send(
            "POST",
            f"/repos/{repository}/pulls",
            json={"head": head, "base": base, "title": title, "body": body, "draft": draft},
        )
        if response.status_code == 422 and "already exists" in response.text:
            owner = repository.split("/")[0]
            existing = self._request(
                "GET",
                f"/repos/{repository}/pulls",
                f"Repository not found on GitHub: {repository}",
                params={"head": f"{owner}:{head}", "state": "open"},
            )
            if existing:
                return _remote_pull(repository, existing[0]), False
        data = self._json(response, f"Repository not found on GitHub: {repository}")
        return _remote_pull(repository, data), True
