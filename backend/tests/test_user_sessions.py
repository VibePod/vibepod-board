"""Signing in as a user, the forced password change, and what ends a session."""

from conftest import USER_PASSWORD, add_user, api_routes, as_user, depends_on, login
from fastapi.testclient import TestClient
from sqlmodel import Session

from vibepod_board.auth import SessionManager, require_access
from vibepod_board.services import projects, users

OPEN_ROUTES = {
    "GET /api/health",
    "POST /api/auth/login",
    "POST /api/auth/logout",
    "GET /api/auth/me",
    "POST /api/auth/password",
    "GET /api/permission-areas",
    "GET /api/mcp-info",
    "GET /api/mcp-tools",
}


def temporary_user(session: Session, username: str = "rebecca", *project_ids: str):
    """A user who has not signed in yet. Returns the id and the temporary password."""
    created = users.create_user(session, username, list(project_ids), "admin")
    return created.item.id, created.temporary_password


def attempt(client: TestClient, username: str, password: str) -> int:
    """The status of a sign-in from a fresh browser."""
    browser = TestClient(client.app)
    return browser.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).status_code


def project_keys(browser: TestClient) -> list[str]:
    return [item["key"] for item in browser.get("/api/projects").json()["items"]]


def test_the_admin_signs_in_as_before(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "secret"})

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "username": "admin",
        "role": "admin",
        "mustChangePassword": False,
    }
    assert client.get("/api/auth/me").json() == response.json()


def test_a_new_user_signs_in_and_is_told_to_set_a_password(
    client: TestClient, session: Session
) -> None:
    _, password = temporary_user(session)

    response = client.post("/api/auth/login", json={"username": "Rebecca", "password": password})

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "username": "rebecca",
        "role": "user",
        "mustChangePassword": True,
    }
    assert client.get("/api/auth/me").json() == response.json()


def test_every_failed_sign_in_gets_the_same_answer(client: TestClient, session: Session) -> None:
    user_id, password = temporary_user(session)

    wrong = client.post("/api/auth/login", json={"username": "rebecca", "password": "wrong"})
    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": password})
    users.update_user(session, user_id, active=False)
    deactivated = client.post("/api/auth/login", json={"username": "rebecca", "password": password})

    for response in (wrong, unknown, deactivated):
        assert response.status_code == 401
        assert response.json() == {"error": "Invalid username or password"}
    assert client.get("/api/auth/me").json() == {"authenticated": False}


def test_nothing_but_the_password_change_works_with_a_temporary_password(
    client: TestClient, session: Session
) -> None:
    project = projects.create_project(session, "APP", "App")
    _, password = temporary_user(session, "rebecca", project.id)
    browser = as_user(client, "rebecca", password)

    for method, path in (
        ("GET", "/api/projects"),
        ("GET", "/api/board"),
        ("POST", "/api/ideas"),
        # An admin-only route: the password comes first there too.
        ("GET", "/api/tokens"),
    ):
        response = browser.request(method, path)
        assert response.status_code == 403, path
        assert response.json() == {"error": "Set a new password first"}

    changed = browser.post(
        "/api/auth/password", json={"currentPassword": password, "newPassword": USER_PASSWORD}
    )

    assert changed.status_code == 200
    assert changed.json() == {
        "authenticated": True,
        "username": "rebecca",
        "role": "user",
        "mustChangePassword": False,
    }
    assert project_keys(browser) == ["APP"]


def test_every_route_but_the_open_ones_asks_who_is_calling(client: TestClient) -> None:
    """`require_access` is where a user with a temporary password is refused. A route that
    skipped it would be open to them, so the open routes are counted here."""
    unguarded = {
        f"{method} {route.path}"
        for route in api_routes(client.app.routes)  # type: ignore[attr-defined]
        if route.include_in_schema and not depends_on(route.dependant, require_access)
        for method in route.methods
    }

    assert unguarded == OPEN_ROUTES


def test_a_password_change_follows_the_rules(client: TestClient, session: Session) -> None:
    _, password = temporary_user(session)
    browser = as_user(client, "rebecca", password)

    def change(current: str, new: str) -> int:
        return browser.post(
            "/api/auth/password", json={"currentPassword": current, "newPassword": new}
        ).status_code

    assert change("wrong", USER_PASSWORD) == 403
    assert change(password, "short") == 400
    assert change(password, password) == 400
    assert change(password, USER_PASSWORD) == 200
    assert attempt(client, "rebecca", password) == 401
    assert attempt(client, "rebecca", USER_PASSWORD) == 200


def test_a_password_change_ends_the_users_other_sessions(
    client: TestClient, session: Session
) -> None:
    add_user(session, "rebecca")
    here = as_user(client, "rebecca")
    there = as_user(client, "rebecca")

    changed = here.post(
        "/api/auth/password",
        json={"currentPassword": USER_PASSWORD, "newPassword": "another long password"},
    )

    assert changed.status_code == 200
    assert here.get("/api/projects").status_code == 200
    assert there.get("/api/projects").status_code == 401
    assert there.get("/api/auth/me").json() == {"authenticated": False}


def test_only_a_signed_in_user_changes_a_password_here(client: TestClient) -> None:
    body = {"currentPassword": "secret", "newPassword": USER_PASSWORD}

    assert client.post("/api/auth/password", json=body).status_code == 401

    admin = login(client)
    response = admin.post("/api/auth/password", json=body)

    assert response.status_code == 403
    assert response.json() == {"error": "The admin password is set in the server configuration"}


def test_a_deactivated_user_is_out_at_the_next_request(
    client: TestClient, session: Session
) -> None:
    user_id = add_user(session, "rebecca")
    browser = as_user(client, "rebecca")
    assert browser.get("/api/projects").status_code == 200

    # At the service level, so the session itself still exists.
    users.update_user(session, user_id, active=False)

    assert browser.get("/api/projects").status_code == 401
    assert browser.get("/api/auth/me").json() == {"authenticated": False}


def test_a_change_of_projects_applies_to_the_next_request(
    client: TestClient, session: Session
) -> None:
    app = projects.create_project(session, "APP", "App")
    api = projects.create_project(session, "API", "Api")
    user_id = add_user(session, "rebecca", app.id)
    browser = as_user(client, "rebecca")
    assert project_keys(browser) == ["APP"]

    users.update_user(session, user_id, project_ids=[api.id])

    assert project_keys(browser) == ["API"]


def test_logging_out_ends_a_users_session(client: TestClient, session: Session) -> None:
    add_user(session, "rebecca")
    browser = as_user(client, "rebecca")

    assert browser.post("/api/auth/logout").json() == {"authenticated": False}
    assert browser.get("/api/projects").status_code == 401


def test_the_sessions_of_one_user_end_together() -> None:
    sessions = SessionManager("admin", "secret")
    admin = sessions.login("admin", "secret")
    first = sessions.open_for_user("user-1", "rebecca")
    second = sessions.open_for_user("user-1", "rebecca")
    other = sessions.open_for_user("user-2", "jonas")

    sessions.end_user_sessions("user-1", keep=second.id)

    assert admin is not None and admin.user_id is None
    assert sessions.authenticate(first.id) is None
    assert sessions.authenticate(second.id) == second
    assert sessions.authenticate(other.id) == other
    assert sessions.authenticate(admin.id) == admin

    sessions.end_user_sessions("user-1")

    assert sessions.authenticate(second.id) is None
