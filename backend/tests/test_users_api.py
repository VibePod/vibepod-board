"""The admin manages users; a user works in the assigned projects and nowhere else."""

import re

from conftest import USER_PASSWORD, add_user, api_routes, as_user, depends_on, login
from fastapi.testclient import TestClient
from sqlmodel import Session

from vibepod_board.access import admin_access
from vibepod_board.auth import require_admin_access
from vibepod_board.services import ideas, projects, tokens

ADMIN = admin_access("admin")


def attempt(client: TestClient, username: str, password: str) -> int:
    """The status of a sign-in from a fresh browser."""
    browser = TestClient(client.app)
    return browser.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).status_code


def test_the_admin_creates_a_user_and_sees_the_temporary_password_once(
    client: TestClient, session: Session
) -> None:
    project = projects.create_project(session, "APP", "App")
    admin = login(client)

    created = admin.post("/api/users", json={"username": "Rebecca", "projectIds": [project.id]})

    assert created.status_code == 201
    body = created.json()
    assert re.fullmatch(r"[A-Za-z0-9_-]{16}", body["temporaryPassword"])
    item = body["item"]
    assert item["username"] == "rebecca"
    assert item["active"] is True
    assert item["mustChangePassword"] is True
    assert item["projectIds"] == [project.id]
    assert "lastLoginAt" not in item
    assert "passwordChangedAt" not in item

    listed = admin.get("/api/users").json()["items"]

    assert listed == [item]
    assert body["temporaryPassword"] not in admin.get("/api/users").text


def test_the_admin_sees_the_last_sign_in_and_whether_the_password_is_set(
    client: TestClient,
) -> None:
    admin = login(client)
    temporary = admin.post("/api/users", json={"username": "rebecca"}).json()["temporaryPassword"]

    browser = as_user(client, "rebecca", temporary)
    [signed_in] = admin.get("/api/users").json()["items"]

    assert signed_in["lastLoginAt"]
    assert signed_in["mustChangePassword"] is True

    browser.post(
        "/api/auth/password", json={"currentPassword": temporary, "newPassword": USER_PASSWORD}
    )
    [ready] = admin.get("/api/users").json()["items"]

    assert ready["mustChangePassword"] is False
    assert ready["passwordChangedAt"]


def test_refuses_a_taken_name_the_admins_name_a_malformed_one_and_a_missing_project(
    client: TestClient,
) -> None:
    admin = login(client)

    assert admin.post("/api/users", json={"username": "rebecca"}).status_code == 201
    assert admin.post("/api/users", json={"username": "REBECCA"}).status_code == 409
    assert admin.post("/api/users", json={"username": "Admin"}).status_code == 409
    assert admin.post("/api/users", json={"username": "r b"}).status_code == 400
    assert admin.post("/api/users", json={}).status_code == 400
    missing = admin.post("/api/users", json={"username": "jonas", "projectIds": ["missing"]})
    assert missing.status_code == 404
    assert [user["username"] for user in admin.get("/api/users").json()["items"]] == ["rebecca"]


def test_the_admin_changes_the_projects_of_a_user(client: TestClient, session: Session) -> None:
    app = projects.create_project(session, "APP", "App")
    api = projects.create_project(session, "API", "Api")
    user_id = add_user(session, "rebecca", app.id)
    browser = as_user(client, "rebecca")
    admin = login(client)

    updated = admin.patch(f"/api/users/{user_id}", json={"projectIds": [api.id]})

    assert updated.status_code == 200
    assert updated.json()["item"]["projectIds"] == [api.id]
    assert [item["key"] for item in browser.get("/api/projects").json()["items"]] == ["API"]
    assert admin.patch("/api/users/missing", json={"active": False}).status_code == 404


def test_a_reset_gives_a_new_temporary_password_and_signs_the_user_out(
    client: TestClient, session: Session
) -> None:
    user_id = add_user(session, "rebecca")
    browser = as_user(client, "rebecca")
    admin = login(client)

    reset = admin.post(f"/api/users/{user_id}/reset-password")

    assert reset.status_code == 200
    assert reset.json()["item"]["mustChangePassword"] is True
    temporary = reset.json()["temporaryPassword"]
    assert browser.get("/api/projects").status_code == 401
    assert attempt(client, "rebecca", USER_PASSWORD) == 401
    again = as_user(client, "rebecca", temporary)
    assert again.get("/api/projects").status_code == 403
    assert again.get("/api/auth/me").json()["mustChangePassword"] is True


def test_deactivating_signs_out_and_blocks_sign_in_until_activated(
    client: TestClient, session: Session
) -> None:
    user_id = add_user(session, "rebecca")
    browser = as_user(client, "rebecca")
    admin = login(client)

    off = admin.patch(f"/api/users/{user_id}", json={"active": False})

    assert off.json()["item"]["active"] is False
    assert browser.get("/api/projects").status_code == 401
    assert attempt(client, "rebecca", USER_PASSWORD) == 401

    on = admin.patch(f"/api/users/{user_id}", json={"active": True})

    assert on.json()["item"]["active"] is True
    assert attempt(client, "rebecca", USER_PASSWORD) == 200


def test_deleting_removes_the_user(client: TestClient, session: Session) -> None:
    user_id = add_user(session, "rebecca")
    browser = as_user(client, "rebecca")
    admin = login(client)

    deleted = admin.delete(f"/api/users/{user_id}")

    assert deleted.status_code == 200
    assert deleted.json() == {"id": user_id, "username": "rebecca"}
    assert admin.get("/api/users").json()["items"] == []
    assert browser.get("/api/projects").status_code == 401
    assert attempt(client, "rebecca", USER_PASSWORD) == 401
    assert admin.delete(f"/api/users/{user_id}").status_code == 404


def test_a_user_works_in_the_assigned_projects_only(client: TestClient, session: Session) -> None:
    app = projects.create_project(session, "APP", "App")
    api = projects.create_project(session, "API", "Api")
    ideas.create_idea(session, ADMIN, title="App task", project_id=app.id)
    ideas.create_idea(session, ADMIN, title="Api task", project_id=api.id)
    add_user(session, "rebecca", app.id)
    browser = as_user(client, "rebecca")

    assert [item["key"] for item in browser.get("/api/projects").json()["items"]] == ["APP"]
    assert [item["title"] for item in browser.get("/api/ideas").json()["items"]] == ["App task"]

    created = browser.post("/api/ideas", json={"projectId": app.id, "title": "From Rebecca"})
    refused = browser.post("/api/ideas", json={"projectId": api.id, "title": "Elsewhere"})

    assert created.status_code == 201
    assert refused.status_code == 403
    assert refused.json() == {"error": f"You have no access to project: {api.id}"}
    assert browser.get(f"/api/board?projectId={api.id}").status_code == 403
    assert browser.get(f"/api/board?projectId={app.id}").status_code == 200


def test_a_user_without_projects_sees_an_empty_board(client: TestClient, session: Session) -> None:
    projects.create_project(session, "APP", "App")
    add_user(session, "rebecca")
    browser = as_user(client, "rebecca")

    assert browser.get("/api/projects").json()["items"] == []
    assert browser.get("/api/ideas").json()["items"] == []
    assert browser.post("/api/ideas", json={"title": "Nowhere"}).status_code == 403


def test_every_admin_route_refuses_a_user(client: TestClient, session: Session) -> None:
    add_user(session, "rebecca")
    browser = as_user(client, "rebecca")
    admin_routes = sorted(
        (method, re.sub(r"\{[^}]+\}", "x", route.path))
        for route in api_routes(client.app.routes)  # type: ignore[attr-defined]
        if depends_on(route.dependant, require_admin_access)
        for method in route.methods
    )

    assert ("POST", "/api/users") in admin_routes
    assert ("POST", "/api/projects") in admin_routes
    # A user manages their own tokens and sign-ins, so those are not the admin's alone.
    assert ("GET", "/api/tokens") not in admin_routes
    for method, path in admin_routes:
        response = browser.request(method, path)
        assert response.status_code == 403, f"{method} {path}"
        assert response.json() == {"error": "Admin access required"}


def test_a_token_cannot_manage_users(client: TestClient, session: Session) -> None:
    project = projects.create_project(session, "APP", "App")
    token = tokens.create_token(session, "Agent", [project.id]).token

    response = client.get("/api/users", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
