"""Port of tests/project-transfer.test.ts."""

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from conftest import reset_schema
from sqlalchemy import Engine, text
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.bundle import ProjectBundle, parse_project_bundle
from vibepod_board.db import migrate
from vibepod_board.enums import BoardColumn
from vibepod_board.errors import Conflict, NotFound
from vibepod_board.services.activity import list_activity
from vibepod_board.services.board import list_cards, move_card, update_card
from vibepod_board.services.documents import create_document
from vibepod_board.services.ideas import create_idea, list_ideas, mark_ready
from vibepod_board.services.projects import create_project, list_projects
from vibepod_board.services.readiness import set_idea_readiness
from vibepod_board.services.tokens import create_token, list_tokens
from vibepod_board.services.transfer import export_project, import_project

admin = admin_access("admin")


class Store:
    """Holds the current session; `reset()` mirrors resetDatabase + initializeDatabase."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.session = Session(engine, expire_on_commit=False)

    def reset(self) -> None:
        self.session.close()
        reset_schema(self.engine)
        migrate(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)

    def close(self) -> None:
        self.session.close()


@pytest.fixture
def store(db: Engine) -> Iterator[Store]:
    created = Store(db)
    yield created
    created.close()


def match_object(actual: Any, expected: Any, path: str = "$") -> None:
    """Vitest `toMatchObject`: objects match as subsets, arrays element-wise and same length."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected object, got {actual!r}"
        for key, value in expected.items():
            assert key in actual, f"{path}.{key}: missing"
            match_object(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected array, got {actual!r}"
        assert len(actual) == len(expected), f"{path}: length {len(actual)} != {len(expected)}"
        for index, (item, value) in enumerate(zip(actual, expected, strict=True)):
            match_object(item, value, f"{path}[{index}]")
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def dump(model: Any) -> Any:
    return model.model_dump(mode="json")


def create_transfer_bundle(session: Session) -> ProjectBundle:
    project = create_project(
        session, key="APP", title="Imported Application", summary="Source data"
    )
    foundation = create_idea(session, admin, project_id=project.id, title="Imported foundation")
    feature = create_idea(
        session, admin, project_id=project.id, title="Imported feature", depends_on=[foundation.id]
    )
    mark_ready(session, admin, foundation.id)
    card = list_cards(session, admin, project.id)[0]
    set_idea_readiness(session, admin, foundation.id, score=8, reason="Ready to move")
    create_document(
        session,
        admin,
        project_id=project.id,
        title="Imported plan",
        linked_idea_ids=[feature.id],
        linked_card_ids=[card.id],
    )
    return export_project(session, project.id)


def test_exports_one_self_contained_project(store: Store) -> None:
    s = store.session
    project = create_project(s, key="APP", title="Application", summary="Portable app project")
    other_project = create_project(s, key="OTH", title="Other")
    foundation = create_idea(s, admin, project_id=project.id, title="Foundation")
    feature = create_idea(
        s, admin, project_id=project.id, title="Feature", depends_on=[foundation.id]
    )
    create_idea(s, admin, project_id=other_project.id, title="Other project task")
    mark_ready(s, admin, foundation.id)
    card = list_cards(s, admin, project.id)[0]
    move_card(s, admin, card.id, BoardColumn("planned"))
    set_idea_readiness(s, admin, foundation.id, score=8, reason="Clear")
    create_document(
        s,
        admin,
        project_id=project.id,
        title="Plan",
        content="Ship it",
        linked_idea_ids=[foundation.id],
        linked_card_ids=[card.id],
    )

    bundle = export_project(s, project.id)
    data = dump(bundle)

    match_object(data, {"bundleVersion": 4, "project": {"id": project.id, "key": "APP"}})
    assert len(bundle.ideas) == 2
    assert [idea.project_id for idea in bundle.ideas] == [project.id, project.id]
    assert next(idea for idea in bundle.ideas if idea.id == feature.id).depends_on == [
        foundation.id
    ]
    assert len(bundle.board_cards) == 1
    assert bundle.board_cards[0].column == "planned"
    assert len(bundle.readiness_events) == 1
    match_object(
        data["documents"][0],
        {"linkedIdeaIds": [foundation.id], "linkedCardIds": [card.id]},
    )
    assert "Other project task" not in json.dumps(data)
    with pytest.raises(NotFound, match="Project not found: missing"):
        export_project(s, "missing")


@pytest.mark.parametrize("column", [BoardColumn.PLANNED, BoardColumn.PR_READY])
def test_imports_a_bundle_as_a_new_project(store: Store, column: BoardColumn) -> None:
    s = store.session
    project = create_project(s, key="APP", title="Application", summary="Portable app project")
    foundation = create_idea(
        s,
        admin,
        project_id=project.id,
        title="Foundation",
        repository_local_path="/workspace/app",
        repository_remote_url="git@github.com:example/app.git",
    )
    feature = create_idea(
        s, admin, project_id=project.id, title="Feature", depends_on=[foundation.id]
    )
    mark_ready(s, admin, foundation.id)
    card = list_cards(s, admin, project.id)[0]
    update_card(s, admin, card.id, column=column, branch_name="vp-115")
    set_idea_readiness(s, admin, foundation.id, score=8, reason="Clear")
    create_document(
        s,
        admin,
        project_id=project.id,
        title="Plan",
        content="Ship it",
        linked_idea_ids=[feature.id],
        linked_card_ids=[card.id],
    )
    bundle = export_project(s, project.id)

    store.reset()
    s = store.session

    result = import_project(s, bundle, replace_existing=False)
    round_trip = export_project(s, bundle.project.id)

    # The review settings stay behind, so the new project starts with the defaults.
    settings = {"requiredApprovals": 1, "maxReviewRounds": 3}
    assert dump(result) == {"item": {**dump(bundle.project), **settings}, "replaced": False}
    expected = dump(bundle)
    match_object(
        dump(round_trip),
        {
            "project": expected["project"],
            "ideas": expected["ideas"],
            "boardCards": expected["boardCards"],
            "readinessEvents": expected["readinessEvents"],
            "documents": expected["documents"],
        },
    )
    match_object(
        dump(list_activity(s, admin)[0]),
        {"type": "project.imported", "message": "Imported project: Application"},
    )


def test_normalises_the_pull_request_repository_on_import(store: Store) -> None:
    s = store.session
    project = create_project(s, key="APP", title="Application", summary="")
    idea = create_idea(s, admin, project_id=project.id, title="Linked")
    mark_ready(s, admin, idea.id)
    bundle = export_project(s, project.id)
    card = bundle.board_cards[0].model_copy(
        update={
            "github_pr_url": "https://github.com/Example/App/pull/5",
            "github_pr_number": 5,
            "github_pr_repository": "Example/App",
            "github_pr_state": "open",
        }
    )
    bundle = bundle.model_copy(update={"board_cards": [card]})

    store.reset()
    s = store.session
    import_project(s, bundle, replace_existing=False)

    [imported] = list_cards(s, admin, project.id)
    assert imported.github_pr_repository == "example/app"


def test_requires_confirmation_and_replaces_a_project_while_retaining_its_identity(
    store: Store,
) -> None:
    bundle = create_transfer_bundle(store.session)
    store.reset()
    s = store.session
    destination = create_project(s, key="APP", title="Old destination")
    create_idea(s, admin, project_id=destination.id, title="Old destination task")
    create_token(s, name="Destination token", project_ids=[destination.id])

    with pytest.raises(Conflict, match="replacement confirmation is required"):
        import_project(s, bundle, replace_existing=False)

    replaced = import_project(s, bundle, replace_existing=True)
    imported_ideas = list_ideas(s, admin, destination.id)

    assert replaced.replaced is True
    match_object(
        dump(replaced.item),
        {"id": destination.id, "key": "APP", "title": bundle.project.title},
    )
    assert "Old destination task" not in [idea.title for idea in imported_ideas]
    assert [idea.project_id for idea in imported_ideas] == [destination.id for _ in bundle.ideas]
    assert destination.id in [item.id for item in list_tokens(s)[0].projects]


def test_rejects_a_new_key_when_the_bundle_project_id_belongs_to_another_project(
    store: Store,
) -> None:
    bundle = create_transfer_bundle(store.session)
    store.reset()
    s = store.session
    other = create_project(s, key="OTH", title="Other")
    data = dump(bundle)
    data["project"]["id"] = other.id
    for key in ("ideas", "boardCards", "documents"):
        for item in data[key]:
            item["projectId"] = other.id
    collision_bundle = parse_project_bundle(data)

    with pytest.raises(Conflict, match=f"Project ID is already used: {other.id}"):
        import_project(s, collision_bundle, replace_existing=False)
    assert [item.key for item in list_projects(s, admin)] == ["OTH"]


def test_rejects_child_id_collisions_with_another_project_before_replacement(
    store: Store,
) -> None:
    bundle = create_transfer_bundle(store.session)
    store.reset()
    s = store.session
    destination = create_project(s, key="APP", title="Old destination")
    create_idea(s, admin, project_id=destination.id, title="Old destination task")
    other = create_project(s, key="OTH", title="Other")
    unrelated = create_idea(s, admin, project_id=other.id, title="Unrelated task")
    replaced_id = bundle.ideas[0].id

    def remap_id(value: str) -> str:
        return unrelated.id if value == replaced_id else value

    remap: Callable[[list[str]], list[str]] = lambda ids: [remap_id(i) for i in ids]  # noqa: E731
    data = dump(bundle)
    for idea in data["ideas"]:
        idea["id"] = remap_id(idea["id"])
        idea["dependsOn"] = remap(idea["dependsOn"])
        idea["blocks"] = remap(idea["blocks"])
        idea["blockedBy"] = remap(idea["blockedBy"])
    for card in data["boardCards"]:
        if card.get("ideaId"):
            card["ideaId"] = remap_id(card["ideaId"])
        else:
            card.pop("ideaId", None)
        card["dependsOn"] = remap(card["dependsOn"])
        card["blockedBy"] = remap(card["blockedBy"])
    for event in data["readinessEvents"]:
        event["ideaId"] = remap_id(event["ideaId"])
    for document in data["documents"]:
        document["linkedIdeaIds"] = remap(document["linkedIdeaIds"])
    collision_bundle = parse_project_bundle(data)

    with pytest.raises(
        Conflict, match=f"Idea ID is already used by another project: {unrelated.id}"
    ):
        import_project(s, collision_bundle, replace_existing=True)
    assert "Old destination task" in [idea.title for idea in list_ideas(s, admin, destination.id)]


def test_rolls_back_replacement_when_an_insertion_fails(store: Store) -> None:
    bundle = create_transfer_bundle(store.session)
    store.reset()
    s = store.session
    destination = create_project(s, key="APP", title="Old destination")
    create_idea(s, admin, project_id=destination.id, title="Old destination task")
    with store.engine.begin() as connection:
        connection.execute(
            text(
                """
                create or replace function reject_project_import_test() returns trigger as $$
                begin
                  if new.title = 'Force transaction failure' then
                    raise exception 'forced project import failure';
                  end if;
                  return new;
                end;
                $$ language plpgsql;
                create trigger reject_project_import_test
                before insert on ideas
                for each row execute function reject_project_import_test();
                """
            )
        )
    data = dump(bundle)
    data["ideas"][0]["title"] = "Force transaction failure"
    failing_bundle = parse_project_bundle(data)

    try:
        with pytest.raises(Exception, match="forced project import failure"):
            import_project(s, failing_bundle, replace_existing=True)
        assert "Old destination task" in [
            idea.title for idea in list_ideas(s, admin, destination.id)
        ]
    finally:
        s.rollback()
        with store.engine.begin() as connection:
            connection.execute(text("drop trigger if exists reject_project_import_test on ideas"))
            connection.execute(text("drop function if exists reject_project_import_test()"))
