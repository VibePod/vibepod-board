"""An in-memory GitHub issues API behind `httpx.MockTransport`."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class FakeGitHub:
    def __init__(self) -> None:
        self.issues: dict[tuple[str, int], dict[str, Any]] = {}
        self.requests: list[tuple[str, str]] = []
        self.clock = T0
        self.status_override: int | None = None
        self.transport = httpx.MockTransport(self._handle)

    def tick(self) -> str:
        self.clock += timedelta(minutes=1)
        return self.clock.isoformat().replace("+00:00", "Z")

    def add_issue(
        self,
        repository: str,
        number: int,
        title: str,
        body: str = "",
        labels: tuple[str, ...] = (),
        state: str = "open",
    ) -> dict[str, Any]:
        issue = {
            "number": number,
            "html_url": f"https://github.com/{repository}/issues/{number}",
            "title": title,
            "body": body,
            "labels": [{"name": label} for label in labels],
            "state": state,
            "updated_at": self.tick(),
        }
        self.issues[(repository, number)] = issue
        return issue

    def edit(self, repository: str, number: int, **changes: Any) -> None:
        issue = self.issues[(repository, number)]
        if "labels" in changes:
            changes["labels"] = [{"name": label} for label in changes["labels"]]
        issue.update(changes, updated_at=self.tick())

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if self.status_override:
            return httpx.Response(self.status_override, json={"message": "nope"})
        parts = request.url.path.strip("/").split("/")  # repos/{owner}/{repo}/issues[/{n}]
        repository = f"{parts[1]}/{parts[2]}"
        if request.method == "POST" and len(parts) == 4:
            payload = json.loads(request.content)
            number = 1 + max((n for (r, n) in self.issues if r == repository), default=0)
            issue = self.add_issue(
                repository, number, payload["title"], payload["body"], tuple(payload["labels"])
            )
            return httpx.Response(201, json=issue)
        key = (repository, int(parts[4]))
        if key not in self.issues:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "PATCH":
            payload = json.loads(request.content)
            self.edit(*key, **payload)
        return httpx.Response(200, json=self.issues[key])
