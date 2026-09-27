"""Port of tests/api.test.ts."""

import json
from typing import Any

from conftest import login, reset_schema
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.db import migrate
from vibepod_board.services.ideas import create_idea
from vibepod_board.services.projects import create_project
from vibepod_board.services.tokens import create_token
from vibepod_board.services.transfer import export_project

admin = admin_access("admin")


def assert_subset(actual: Any, expected: Any, path: str = "$") -> None:
    """Recursive equivalent of vitest's `toMatchObject`."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected object, got {actual!r}"
        for key, value in expected.items():
            assert key in actual, f"{path}: missing key {key!r}"
            assert_subset(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected array, got {actual!r}"
        assert len(actual) == len(expected), f"{path}: length {len(actual)} != {len(expected)}"
        for index, (item, value) in enumerate(zip(actual, expected, strict=True)):
            assert_subset(item, value, f"{path}[{index}]")
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def anon(client: TestClient) -> TestClient:
    """A cookie-less client, like supertest's `request(app)`."""
    return TestClient(client.app)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_project_via_api(agent: TestClient, title: str, key: str) -> dict[str, Any]:
    response = agent.post("/api/projects", json={"title": title, "key": key})
    assert response.status_code == 201, response.text
    return response.json()["item"]


def create_idea_via_api(agent: TestClient, **body: Any) -> dict[str, Any]:
    response = agent.post("/api/ideas", json=body)
    assert response.status_code == 201, response.text
    return response.json()["item"]


def mark_ready_via_api(agent: TestClient, idea_id: str, body: Any = None) -> dict[str, Any]:
    response = agent.post(f"/api/ideas/{idea_id}/ready", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def get_board(agent: TestClient, **params: str) -> dict[str, Any]:
    response = agent.get("/api/board", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_requires_admin_auth_for_rest_board_endpoints(client: TestClient) -> None:
    assert anon(client).get("/api/health").status_code == 200
    assert anon(client).get("/api/projects").status_code == 401

    agent = login(client)
    assert agent.get("/api/projects").status_code == 200
    assert agent.post("/api/auth/logout").status_code == 200
    assert agent.get("/api/projects").status_code == 401


def test_reports_auth_state_through_auth_me(client: TestClient) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json() == {"authenticated": False}

    login(client)

    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "username": "admin"}


def test_exports_projects_only_for_admins(client: TestClient, session: Session) -> None:
    project = create_project(session, "APP", "Application")
    created_token = create_token(session, "Project client", [project.id])

    assert anon(client).get(f"/api/projects/{project.id}/export").status_code == 401
    response = anon(client).get(
        f"/api/projects/{project.id}/export", headers=bearer(created_token.token)
    )
    assert response.status_code == 403

    agent = login(client)
    response = agent.get(f"/api/projects/{project.id}/export")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    assert response.headers["content-disposition"] == 'attachment; filename="APP-project.json"'
    expected = {"bundleVersion": 3, "project": {"id": project.id, "key": "APP"}}
    assert_subset(response.json(), expected)
    assert agent.get("/api/projects/missing/export").status_code == 404


def test_creates_and_replaces_projects_through_import_endpoint(
    client: TestClient, session: Session, engine: Engine
) -> None:
    source = create_project(session, "APP", "Application")
    create_idea(session, admin, title="Portable task", project_id=source.id)
    bundle = export_project(session, source.id).model_dump(mode="json")
    created_token = create_token(session, "Project client", [source.id])
    session.close()

    payload = {"bundle": bundle, "replaceExisting": False}
    assert anon(client).post("/api/projects/import", json=payload).status_code == 401
    response = anon(client).post(
        "/api/projects/import", json=payload, headers=bearer(created_token.token)
    )
    assert response.status_code == 403

    reset_schema(engine)
    migrate(engine)
    agent = login(client)
    created = agent.post("/api/projects/import", json=payload)
    assert created.status_code == 201, created.text
    assert_subset(created.json(), {"item": {"id": source.id, "key": "APP"}, "replaced": False})

    assert agent.post("/api/projects/import", json=payload).status_code == 409
    replaced = agent.post("/api/projects/import", json={"bundle": bundle, "replaceExisting": True})
    assert replaced.status_code == 200, replaced.text
    assert_subset(replaced.json(), {"item": {"id": source.id, "key": "APP"}, "replaced": True})


def test_validates_and_limits_project_import_payloads(client: TestClient) -> None:
    agent = login(client)

    response = agent.post(
        "/api/projects/import",
        json={"bundle": {"bundleVersion": 99}, "replaceExisting": False},
    )
    assert response.status_code == 400

    response = agent.post(
        "/api/projects/import",
        headers={"Content-Type": "application/json"},
        content=json.dumps({"bundle": {"padding": "x" * (10 * 1024 * 1024)}}),
    )
    assert response.status_code == 413
    assert response.json() == {"error": "Project import file must be 10 MiB or smaller"}

    document_response = agent.post(
        "/api/documents",
        headers={"Content-Type": "application/json"},
        content=json.dumps({"content": "x" * (3 * 1024 * 1024)}),
    )
    assert document_response.status_code == 413
    assert document_response.json() == {"error": "Request body must be 2 MiB or smaller"}


def test_allows_project_tokens_to_access_only_mapped_project_data(client: TestClient) -> None:
    agent = login(client)

    app_project = create_project_via_api(agent, "App", "APP")
    api_project = create_project_via_api(agent, "API", "API")
    create_idea_via_api(agent, projectId=app_project["id"], title="Visible")
    create_idea_via_api(agent, projectId=api_project["id"], title="Hidden")

    token_response = agent.post(
        "/api/tokens", json={"name": "Codex", "projectIds": [app_project["id"]]}
    )
    assert token_response.status_code == 201
    headers = bearer(token_response.json()["token"])
    other = anon(client)

    token_projects = other.get("/api/projects", headers=headers)
    assert token_projects.status_code == 200
    assert [project["id"] for project in token_projects.json()["items"]] == [app_project["id"]]

    token_ideas = other.get("/api/ideas", headers=headers)
    assert token_ideas.status_code == 200
    assert [idea["title"] for idea in token_ideas.json()["items"]] == ["Visible"]

    response = other.post(
        "/api/ideas",
        headers=headers,
        json={"projectId": api_project["id"], "title": "Forbidden"},
    )
    assert response.status_code == 403

    response = other.post("/api/projects", headers=headers, json={"key": "NEW", "title": "Nope"})
    assert response.status_code == 403


def test_validates_project_ids_on_create_and_update(client: TestClient) -> None:
    agent = login(client)

    assert agent.post("/api/projects", json={"title": "Missing key"}).status_code == 400
    response = agent.post("/api/projects", json={"title": "Invalid key", "key": "app"})
    assert response.status_code == 400

    created = create_project_via_api(agent, "Launch site", "LS")
    assert created["key"] == "LS"

    response = agent.post("/api/projects", json={"title": "Duplicate", "key": "LS"})
    assert response.status_code == 409

    updated = agent.patch(f"/api/projects/{created['id']}", json={"key": "WEB"})
    assert updated.status_code == 200
    assert updated.json()["item"]["key"] == "WEB"


def test_creates_projects_and_scopes_project_work_through_query_params(
    client: TestClient,
) -> None:
    agent = login(client)

    project = agent.post(
        "/api/projects",
        json={"title": "Launch site", "key": "LS", "summary": "Coordinate launch work"},
    )
    assert project.status_code == 201
    project_id = project.json()["item"]["id"]
    other_project_id = create_project_via_api(agent, "Backlog", "BL")["id"]

    task = create_idea_via_api(agent, projectId=project_id, title="Publish landing page")
    create_idea_via_api(agent, projectId=other_project_id, title="Backlog task")
    mark_ready_via_api(agent, task["id"])
    response = agent.post(
        "/api/documents", json={"projectId": project_id, "title": "Launch notes", "kind": "notes"}
    )
    assert response.status_code == 201

    projects = agent.get("/api/projects")
    assert projects.status_code == 200
    assert [item["title"] for item in projects.json()["items"]] == ["Backlog", "Launch site"]

    scoped_ideas = agent.get(f"/api/ideas?projectId={project_id}")
    assert scoped_ideas.status_code == 200
    assert len(scoped_ideas.json()["items"]) == 1
    assert scoped_ideas.json()["items"][0]["projectId"] == project_id

    scoped_board = agent.get(f"/api/board?projectId={project_id}")
    assert scoped_board.status_code == 200
    assert len(scoped_board.json()["columns"]["ready"]) == 1
    assert scoped_board.json()["columns"]["ready"][0]["projectId"] == project_id

    scoped_documents = agent.get(f"/api/documents?projectId={project_id}")
    assert scoped_documents.status_code == 200
    assert len(scoped_documents.json()["items"]) == 1
    assert scoped_documents.json()["items"][0]["projectId"] == project_id


def test_toggles_board_availability_through_ready_endpoint(client: TestClient) -> None:
    agent = login(client)
    project_id = create_project_via_api(agent, "Launch site", "LS")["id"]
    idea_id = create_idea_via_api(agent, projectId=project_id, title="Publish launch checklist")[
        "id"
    ]

    ready = mark_ready_via_api(agent, idea_id, {"available": True})
    assert ready["item"]["status"] == "ready"

    board_with_task = get_board(agent, projectId=project_id)
    assert len(board_with_task["columns"]["ready"]) == 1
    assert board_with_task["columns"]["ready"][0]["ideaId"] == idea_id

    unavailable = mark_ready_via_api(agent, idea_id, {"available": False})
    assert unavailable["item"]["status"] == "idea"

    board_without_task = get_board(agent, projectId=project_id)
    assert len(board_without_task["columns"]["ready"]) == 0


def test_updates_a_task_to_denied_status(client: TestClient) -> None:
    agent = login(client)
    created = create_idea_via_api(agent, title="Add confetti animation")

    response = agent.patch(f"/api/ideas/{created['id']}", json={"status": "denied"})
    assert response.status_code == 200
    assert response.json()["item"]["status"] == "denied"

    alias_response = agent.patch(f"/api/ideas/{created['id']}", json={"status": "dennied"})
    assert alias_response.status_code == 200
    assert alias_response.json()["item"]["status"] == "denied"


def test_creates_ideas_and_lists_them(client: TestClient) -> None:
    agent = login(client)

    create_idea_via_api(
        agent, title="Kanban processing", summary="Ready issues move through columns"
    )

    response = agent.get("/api/ideas")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["title"] == "Kanban processing"


def test_updates_board_card_branch_names_through_board_api(client: TestClient) -> None:
    agent = login(client)
    created = create_idea_via_api(agent, title="Lifecycle state")
    mark_ready_via_api(agent, created["id"])
    card_id = get_board(agent)["columns"]["ready"][0]["id"]

    updated = agent.patch(f"/api/board/{card_id}", json={"branchName": "vp-task-lifecycle-state"})
    assert updated.status_code == 200
    assert updated.json()["item"]["branchName"] == "vp-task-lifecycle-state"

    listed = get_board(agent)
    assert listed["columns"]["ready"][0]["branchName"] == "vp-task-lifecycle-state"


REPOSITORY_UPDATE = {
    "details": "Implemented in vibepod-cli",
    "repositoryLocalPath": "/workspace/vibepod-cli",
    "repositoryRemoteUrl": "git@github.com:vibepod/vibepod-cli.git",
}


def test_updates_task_repository_metadata_and_syncs_it_to_the_board(client: TestClient) -> None:
    agent = login(client)
    created = create_idea_via_api(
        agent,
        title="Lifecycle state",
        details="Initial task details",
        repositoryLocalPath="/workspace/old",
        repositoryRemoteUrl="git@github.com:vibepod/old.git",
    )

    mark_ready_via_api(agent, created["id"])

    updated = agent.patch(f"/api/ideas/{created['id']}", json=REPOSITORY_UPDATE)
    assert updated.status_code == 200
    assert_subset(updated.json()["item"], REPOSITORY_UPDATE)

    listed = get_board(agent)
    assert_subset(listed["columns"]["ready"][0], REPOSITORY_UPDATE)


def test_updates_board_card_implementation_repository_metadata(client: TestClient) -> None:
    agent = login(client)
    created = create_idea_via_api(agent, title="Lifecycle state")
    mark_ready_via_api(agent, created["id"])
    card_id = get_board(agent)["columns"]["ready"][0]["id"]

    updated = agent.patch(f"/api/board/{card_id}", json=REPOSITORY_UPDATE)
    assert updated.status_code == 200
    assert_subset(updated.json()["item"], REPOSITORY_UPDATE)


def test_creates_documents_through_the_api(client: TestClient) -> None:
    agent = login(client)

    response = agent.post(
        "/api/documents",
        json={
            "title": "Execution plan",
            "kind": "execution_plan",
            "content": "Step-by-step implementation plan",
        },
    )
    assert response.status_code == 201
    assert response.json()["item"]["title"] == "Execution plan"
    assert response.json()["item"]["kind"] == "execution_plan"


def test_sets_board_card_readiness_through_the_api(client: TestClient) -> None:
    agent = login(client)

    project = create_project_via_api(agent, "App", "APP")
    idea = create_idea_via_api(agent, projectId=project["id"], title="Scored")
    mark_ready_via_api(agent, idea["id"])
    card = get_board(agent)["columns"]["ready"][0]

    response = agent.post(
        f"/api/board/{card['id']}/readiness",
        json={"score": 8, "reason": "Acceptance criteria and repo present"},
    )
    assert response.status_code == 200
    item = response.json()["item"]
    assert_subset(
        item,
        {
            "id": card["id"],
            "readinessScore": 8,
            "readinessReason": "Acceptance criteria and repo present",
        },
    )
    assert "readinessEvaluatedAt" in item
    assert item["updatedAt"] == card["updatedAt"]

    url = f"/api/board/{card['id']}/readiness"
    assert agent.post(url, json={"score": 42, "reason": "r"}).status_code == 400
    assert agent.post(url, json={"score": 5}).status_code == 400
    assert anon(client).post(url, json={"score": 5, "reason": "r"}).status_code == 401


def test_sets_idea_readiness_and_mirrors_it_onto_the_linked_card(client: TestClient) -> None:
    agent = login(client)

    project = create_project_via_api(agent, "App", "APP")
    idea = create_idea_via_api(agent, projectId=project["id"], title="Scored idea")
    mark_ready_via_api(agent, idea["id"])

    url = f"/api/ideas/{idea['id']}/readiness"
    response = agent.post(url, json={"score": 6, "reason": "Reasonable"})
    assert response.status_code == 200
    item = response.json()["item"]
    assert_subset(item, {"id": idea["id"], "readinessScore": 6, "readinessReason": "Reasonable"})
    assert "readinessEvaluatedAt" in item

    card = get_board(agent)["columns"]["ready"][0]
    assert card["readinessScore"] == 6
    assert card["readinessReason"] == "Reasonable"

    assert agent.post(url, json={"score": 99, "reason": "x"}).status_code == 400
    assert anon(client).post(url, json={"score": 5, "reason": "r"}).status_code == 401


def test_lists_idea_readiness_history_newest_first(client: TestClient) -> None:
    agent = login(client)

    project = create_project_via_api(agent, "App", "APP")
    idea = create_idea_via_api(agent, projectId=project["id"], title="Tracked")

    url = f"/api/ideas/{idea['id']}/readiness"
    assert agent.post(url, json={"score": 4, "reason": "Rough"}).status_code == 200
    assert agent.post(url, json={"score": 8, "reason": "Refined"}).status_code == 200

    history = agent.get(url)
    assert history.status_code == 200
    items = history.json()["items"]
    assert len(items) == 2
    assert_subset(items[0], {"score": 8, "reason": "Refined"})
    assert_subset(items[1], {"score": 4, "reason": "Rough"})
    assert "createdAt" in items[0]

    assert anon(client).get(url).status_code == 401


def test_manages_task_dependencies_through_the_api(client: TestClient) -> None:
    agent = login(client)

    project_id = create_project_via_api(agent, "App", "APP")["id"]
    schema = create_idea_via_api(agent, projectId=project_id, title="Schema")
    api = create_idea_via_api(agent, projectId=project_id, title="API", dependsOn=[schema["id"]])
    assert api["dependsOn"] == [schema["id"]]
    assert api["blockedBy"] == [schema["id"]]

    docs = create_idea_via_api(agent, projectId=project_id, title="Docs")
    added = agent.post(f"/api/ideas/{docs['id']}/dependencies", json={"dependsOnId": api["id"]})
    assert added.status_code == 201
    assert added.json()["item"]["dependsOn"] == [api["id"]]

    replaced = agent.put(
        f"/api/ideas/{docs['id']}/dependencies",
        json={"dependsOnIds": [schema["id"], api["id"]]},
    )
    assert replaced.status_code == 200
    assert sorted(replaced.json()["item"]["dependsOn"]) == sorted([schema["id"], api["id"]])

    removed = agent.delete(f"/api/ideas/{docs['id']}/dependencies/{schema['id']}")
    assert removed.status_code == 200
    assert removed.json()["item"]["dependsOn"] == [api["id"]]

    response = anon(client).post(
        f"/api/ideas/{docs['id']}/dependencies", json={"dependsOnId": schema["id"]}
    )
    assert response.status_code == 401


def test_rejects_self_cross_project_and_cyclic_dependencies(client: TestClient) -> None:
    agent = login(client)

    project = create_project_via_api(agent, "App", "APP")
    other_project = create_project_via_api(agent, "Ops", "OPS")
    schema = create_idea_via_api(agent, projectId=project["id"], title="Schema")
    api = create_idea_via_api(agent, projectId=project["id"], title="API")
    outside = create_idea_via_api(agent, projectId=other_project["id"], title="Outside")

    url = f"/api/ideas/{api['id']}/dependencies"
    assert agent.post(url, json={"dependsOnId": api["id"]}).status_code == 400
    assert agent.post(url, json={"dependsOnId": outside["id"]}).status_code == 400
    assert agent.post(url, json={"dependsOnId": "missing"}).status_code == 404

    assert agent.post(url, json={"dependsOnId": schema["id"]}).status_code == 201
    cycle = agent.post(f"/api/ideas/{schema['id']}/dependencies", json={"dependsOnId": api["id"]})
    assert cycle.status_code == 409
    assert "Dependency cycle detected" in cycle.json()["error"]


def test_serves_a_dependency_resolved_work_order(client: TestClient) -> None:
    agent = login(client)

    project_id = create_project_via_api(agent, "App", "APP")["id"]
    schema = create_idea_via_api(agent, projectId=project_id, title="Schema")
    api = create_idea_via_api(agent, projectId=project_id, title="API", dependsOn=[schema["id"]])

    work_order = agent.get("/api/work-order", params={"projectId": project_id})
    assert work_order.status_code == 200
    assert [item["id"] for item in work_order.json()["items"]] == [schema["id"], api["id"]]
    assert_subset(
        work_order.json()["items"][1],
        {"taskId": "APP-2", "position": 2, "wave": 1, "isBlocked": True, "isActionable": False},
    )
    assert work_order.json()["cyclicTaskIds"] == []

    mark_ready_via_api(agent, schema["id"], {"available": True})
    board = get_board(agent, projectId=project_id)
    card_id = board["columns"]["ready"][0]["id"]
    response = agent.patch(f"/api/board/{card_id}", json={"column": "done"})
    assert response.status_code == 200

    unblocked = agent.get("/api/work-order", params={"projectId": project_id})
    assert unblocked.status_code == 200
    assert_subset(unblocked.json()["items"][1], {"isBlocked": False, "isActionable": True})

    assert anon(client).get("/api/work-order").status_code == 401
