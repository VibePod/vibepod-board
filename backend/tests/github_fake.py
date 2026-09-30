"""An in-memory GitHub issues and pulls API behind `httpx.MockTransport`."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import httpx

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class FakeGitHub:
    def __init__(self) -> None:
        self.issues: dict[tuple[str, int], dict[str, Any]] = {}
        self.pulls: dict[tuple[str, int], dict[str, Any]] = {}
        # Pushed branches per repository; the first one is the default branch.
        self.branches: dict[str, list[str]] = {}
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

    def add_pull(
        self,
        repository: str,
        head: str,
        base: str = "main",
        title: str = "PR",
        body: str = "",
        draft: bool = False,
        state: str = "open",
        merged: bool = False,
        head_repository: str | None = None,
    ) -> dict[str, Any]:
        # Issues and pulls share their numbers on GitHub.
        taken = [n for (r, n) in (*self.issues, *self.pulls) if r == repository]
        number = 1 + max(taken, default=0)
        pull = {
            "number": number,
            "html_url": f"https://github.com/{repository}/pull/{number}",
            "title": title,
            "body": body,
            "state": state,
            "draft": draft,
            "merged_at": self.tick() if merged else None,
            "head": {"ref": head, "repo": {"full_name": head_repository or repository}},
            "base": {"ref": base},
        }
        self.pulls[(repository, number)] = pull
        return pull

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if self.status_override:
            return httpx.Response(self.status_override, json={"message": "nope"})
        # Branch names may hold slashes, so they are read from the raw, still quoted path.
        parts = request.url.raw_path.decode().split("?")[0].strip("/").split("/")
        repository = f"{parts[1]}/{parts[2]}"
        if len(parts) == 3:
            if repository not in self.branches:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"default_branch": self.branches[repository][0]})
        if parts[3] == "branches":
            if unquote(parts[4]) not in self.branches.get(repository, []):
                return httpx.Response(404, json={"message": "Branch not found"})
            return httpx.Response(200, json={"name": unquote(parts[4])})
        if parts[3] == "pulls":
            return self._handle_pulls(request, repository, parts[4:])
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

    def _handle_pulls(
        self, request: httpx.Request, repository: str, rest: list[str]
    ) -> httpx.Response:
        if rest:
            pull = self.pulls.get((repository, int(rest[0])))
            if pull is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=pull)
        open_pulls = [
            pull
            for (r, _), pull in self.pulls.items()
            if r == repository and pull["state"] == "open"
        ]
        if request.method == "GET":
            head = request.url.params["head"].split(":", 1)[1]
            return httpx.Response(200, json=[p for p in open_pulls if p["head"]["ref"] == head])
        payload = json.loads(request.content)
        if payload["base"] not in self.branches.get(repository, []):
            return httpx.Response(
                422,
                json={
                    "message": "Validation Failed",
                    "errors": [{"field": "base", "code": "invalid", "message": "base is invalid"}],
                },
            )
        if any(p["head"]["ref"] == payload["head"] for p in open_pulls):
            owner = repository.split("/")[0]
            return httpx.Response(
                422,
                json={
                    "message": "Validation Failed",
                    "errors": [
                        {
                            "resource": "PullRequest",
                            "code": "custom",
                            "message": "A pull request already exists for "
                            f"{owner}:{payload['head']}.",
                        }
                    ],
                },
            )
        pull = self.add_pull(repository, **payload)
        return httpx.Response(201, json=pull)
