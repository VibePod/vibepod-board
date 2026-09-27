"""Optional GitHub issue creation when a ready idea is synced to the board."""

import httpx

from vibepod_board.config import Settings
from vibepod_board.errors import BoardError
from vibepod_board.schemas import Idea
from vibepod_board.services.board import LOCAL, GitHubLink


def issue_body(idea: Idea) -> str:
    sections = []
    if idea.summary:
        sections.append(f"## Summary\n{idea.summary}")
    if idea.details:
        sections.append(f"## Details\n{idea.details}")
    if idea.acceptance_criteria:
        criteria = "\n".join(f"- {item}" for item in idea.acceptance_criteria)
        sections.append(f"## Acceptance Criteria\n{criteria}")
    return "\n\n".join(sections) or "Created from vibepod-board."


def create_issue(settings: Settings, idea: Idea) -> GitHubLink:
    """Opens an issue when GITHUB_TOKEN and GITHUB_REPOSITORY are set; otherwise stays local."""
    if not settings.github_token or not settings.github_repository:
        return LOCAL
    response = httpx.post(
        f"https://api.github.com/repos/{settings.github_repository}/issues",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {settings.github_token}",
            "User-Agent": "vibepod-board",
        },
        json={"title": idea.title, "labels": idea.labels, "body": issue_body(idea)},
        timeout=30,
    )
    if response.is_error:
        raise BoardError(f"GitHub issue creation failed: {response.status_code} {response.text}")
    issue = response.json()
    return GitHubLink(mode="github", issue_url=issue["html_url"], issue_number=issue["number"])
