import copy
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from vibepod_board.bundle import parse_project_bundle

STAMP = "2026-08-14T12:00:00.000Z"


def valid_bundle() -> dict[str, Any]:
    idea = {
        "projectId": "source-project",
        "summary": "",
        "details": "",
        "createdAt": STAMP,
        "updatedAt": STAMP,
    }
    return {
        "bundleVersion": 1,
        "exportedAt": STAMP,
        "project": {
            "id": "source-project",
            "key": "APP",
            "title": "Application",
            "summary": "Portable app project",
            "createdAt": STAMP,
            "updatedAt": STAMP,
        },
        "ideas": [
            {
                **idea,
                "id": "idea-1",
                "taskNumber": 1,
                "title": "Foundation",
                "status": "ready",
                "labels": [],
                "acceptanceCriteria": [],
                "dependsOn": [],
                "blocks": ["idea-2"],
                "blockedBy": [],
            },
            {
                **idea,
                "id": "idea-2",
                "taskNumber": 2,
                "title": "Feature",
                "status": "idea",
                "labels": ["ui"],
                "acceptanceCriteria": ["Works"],
                "dependsOn": ["idea-1"],
                "blocks": [],
                "blockedBy": ["idea-1"],
            },
        ],
        "boardCards": [
            {
                "id": "card-1",
                "projectId": "source-project",
                "ideaId": "idea-1",
                "title": "Foundation",
                "details": "",
                "column": "planned",
                "labels": [],
                "dependsOn": [],
                "blockedBy": [],
                "createdAt": STAMP,
                "updatedAt": STAMP,
            }
        ],
        "readinessEvents": [
            {
                "id": "readiness-1",
                "ideaId": "idea-1",
                "score": 8,
                "reason": "Clear",
                "createdAt": STAMP,
            }
        ],
        "documents": [
            {
                "id": "document-1",
                "projectId": "source-project",
                "title": "Plan",
                "kind": "execution_plan",
                "content": "Ship it",
                "linkedIdeaIds": ["idea-1"],
                "linkedCardIds": ["card-1"],
                "createdAt": STAMP,
                "updatedAt": STAMP,
            }
        ],
    }


def test_accepts_a_self_contained_v1_bundle() -> None:
    assert parse_project_bundle(valid_bundle()).model_dump(mode="json") == valid_bundle()


def test_accepts_a_bundle_without_the_derived_dependency_fields() -> None:
    bundle = valid_bundle()
    for idea in bundle["ideas"]:
        del idea["blocks"], idea["blockedBy"]
    for card in bundle["boardCards"]:
        del card["dependsOn"], card["blockedBy"]

    parsed = parse_project_bundle(bundle)

    assert [(i.id, i.blocks, i.blocked_by) for i in parsed.ideas] == [
        ("idea-1", [], []),
        ("idea-2", [], []),
    ]
    assert parsed.ideas[1].depends_on == ["idea-1"]
    assert (parsed.board_cards[0].depends_on, parsed.board_cards[0].blocked_by) == ([], [])


def _set(path: list[Any], value: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(bundle: dict[str, Any]) -> None:
        target = bundle
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


INVALID_CASES = {
    "unsupported version": _set(["bundleVersion"], 99),
    "foreign idea": _set(["ideas", 0, "projectId"], "other"),
    "missing dependency": _set(["ideas", 1, "dependsOn"], ["missing"]),
    "dependency cycle": _set(["ideas", 0, "dependsOn"], ["idea-2"]),
    "foreign card link": _set(["boardCards", 0, "ideaId"], "missing"),
    "foreign document link": _set(["documents", 0, "linkedIdeaIds"], ["missing"]),
    "duplicate task number": _set(["ideas", 1, "taskNumber"], 1),
    "unknown fields": _set(["mystery"], True),
    "stringly typed number": _set(["ideas", 0, "taskNumber"], "1"),
    "timestamp without offset": _set(["exportedAt"], "2026-08-14T12:00:00"),
}


@pytest.mark.parametrize("mutate", INVALID_CASES.values(), ids=INVALID_CASES.keys())
def test_rejects_invalid_bundles(mutate: Callable[[dict[str, Any]], None]) -> None:
    bundle = copy.deepcopy(valid_bundle())
    mutate(bundle)
    with pytest.raises(ValidationError):
        parse_project_bundle(bundle)


def _linked(bundle: dict[str, Any], version: int, repositories: tuple[str, str]) -> None:
    bundle["bundleVersion"] = version
    for idea, repository in zip(bundle["ideas"], repositories, strict=True):
        idea.update(
            githubIssueUrl="https://github.com/o/r/issues/1",
            githubIssueNumber=1,
            githubRepository=repository,
            githubIssueState="open",
        )


def test_accepts_github_sync_state_in_version_2_only() -> None:
    bundle = valid_bundle()
    _linked(bundle, 2, ("o/r", "o/r"))
    bundle["ideas"][1] = valid_bundle()["ideas"][1]
    assert parse_project_bundle(bundle).ideas[0].github_repository == "o/r"

    bundle["bundleVersion"] = 1
    with pytest.raises(ValidationError, match="GitHub sync fields require bundleVersion 2"):
        parse_project_bundle(bundle)


def test_rejects_two_tasks_linked_to_the_same_issue() -> None:
    bundle = valid_bundle()
    _linked(bundle, 2, ("O/R", "o/r"))
    with pytest.raises(ValidationError, match="Duplicate GitHub issue link: o/r#1"):
        parse_project_bundle(bundle)


def test_accepts_archived_cards_in_version_3_only() -> None:
    bundle = valid_bundle()
    bundle["bundleVersion"] = 3
    bundle["boardCards"][0].update(column="done", archivedAt=STAMP)
    assert parse_project_bundle(bundle).board_cards[0].archived_at is not None

    bundle["bundleVersion"] = 2
    with pytest.raises(ValidationError, match="Archived board cards require bundleVersion 3"):
        parse_project_bundle(bundle)


@pytest.mark.parametrize(
    "field", ["githubPrUrl", "githubPrNumber", "githubPrRepository", "githubPrState"]
)
def test_rejects_any_pull_request_field_before_version_4(field: str) -> None:
    values = {
        "githubPrUrl": "https://github.com/o/r/pull/5",
        "githubPrNumber": 5,
        "githubPrRepository": "o/r",
        "githubPrState": "open",
    }
    bundle = valid_bundle()
    bundle["bundleVersion"] = 3
    bundle["boardCards"][0][field] = values[field]
    with pytest.raises(ValidationError, match="Linked pull requests require bundleVersion 4"):
        parse_project_bundle(bundle)


def test_rejects_archived_cards_outside_the_done_column() -> None:
    bundle = valid_bundle()
    bundle["bundleVersion"] = 3
    bundle["boardCards"][0].update(column="review", archivedAt=STAMP)
    with pytest.raises(ValidationError, match="Archived board card must be in done: card-1"):
        parse_project_bundle(bundle)
