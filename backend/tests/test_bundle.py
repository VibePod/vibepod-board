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
    "unsupported version": _set(["bundleVersion"], 2),
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
