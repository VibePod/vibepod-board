"""Minimal GitHub REST client for issue sync, plus URL helpers.

The token comes from `GITHUB_TOKEN`; it is never stored in the database. Tests pass an
`httpx.MockTransport`, so no test talks to GitHub.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from vibepod_board.errors import BoardError, NotFound

API_URL = "https://api.github.com"
_ISSUE_URL = re.compile(
    r"^https://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(?P<number>\d+)/?$"
)
_REPO_PATH = re.compile(r"^(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


class GitHubUnavailable(BoardError):
    """GitHub refused or failed the request (bad gateway from the board's point of view)."""

    status_code = 502


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
class RemoteIssue:
    ref: IssueRef
    url: str
    title: str
    body: str
    labels: list[str]
    state: str
    updated_at: datetime


def parse_issue_url(url: str) -> IssueRef | None:
    match = _ISSUE_URL.match(url.strip())
    return IssueRef(match["repo"], int(match["number"])) if match else None


def repository_from_remote(remote: str | None) -> str | None:
    """`owner/repo` from an https or scp-style GitHub remote, else None."""
    if not remote:
        return None
    remote = remote.strip()
    for prefix in ("git@github.com:", "https://github.com/", "ssh://git@github.com/"):
        if remote.startswith(prefix):
            match = _REPO_PATH.match(remote[len(prefix) :])
            return match["repo"] if match else None
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


def _remote_issue(repository: str, data: dict[str, Any]) -> RemoteIssue:
    return RemoteIssue(
        ref=IssueRef(repository, int(data["number"])),
        url=data["html_url"],
        title=data["title"],
        body=data.get("body") or "",
        labels=[label["name"] if isinstance(label, dict) else label for label in data["labels"]],
        state=data["state"],
        updated_at=datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00")),
    )


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

    def _request(self, method: str, path: str, ref: IssueRef | str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise GitHubUnavailable(f"GitHub request failed: {error}") from error
        if response.status_code in (401, 403):
            raise GitHubUnavailable("GitHub rejected the token")
        if response.status_code == 404:
            raise NotFound(f"Issue not found on GitHub: {ref}")
        if response.is_error:
            raise GitHubUnavailable(f"GitHub request failed: {response.status_code}")
        return response.json()

    def get_issue(self, ref: IssueRef) -> RemoteIssue:
        data = self._request("GET", f"/repos/{ref.repository}/issues/{ref.number}", ref)
        return _remote_issue(ref.repository, data)

    def create_issue(
        self, repository: str, title: str, body: str, labels: list[str]
    ) -> RemoteIssue:
        data = self._request(
            "POST",
            f"/repos/{repository}/issues",
            repository,
            json={"title": title, "body": body, "labels": labels},
        )
        return _remote_issue(repository, data)

    def update_issue(self, ref: IssueRef, title: str, body: str, labels: list[str]) -> RemoteIssue:
        data = self._request(
            "PATCH",
            f"/repos/{ref.repository}/issues/{ref.number}",
            ref,
            json={"title": title, "body": body, "labels": labels},
        )
        return _remote_issue(ref.repository, data)
