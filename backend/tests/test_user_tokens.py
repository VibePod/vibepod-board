"""A user's own API tokens: only theirs, only for their projects, and only while the user
may sign in."""

from conftest import add_user, as_user, login
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from vibepod_board.services import projects, tokens, users


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def reachable(client: TestClient, token: str) -> list[str] | int:
    """The project keys a token lists, or the status when it is refused."""
    response = TestClient(client.app).get("/api/projects", headers=bearer(token))
    if response.status_code != 200:
        return response.status_code
    return sorted(item["key"] for item in response.json()["items"])


def create(browser: TestClient, name: str, *project_ids: str) -> dict:
    response = browser.post("/api/tokens", json={"name": name, "projectIds": list(project_ids)})
    assert response.status_code == 201, response.text
    return response.json()


def keys(summary: dict) -> list[str]:
    return [project["key"] for project in summary["projects"]]


def test_a_user_creates_a_token_for_their_own_projects(
    client: TestClient, session: Session
) -> None:
    app = projects.create_project(session, "APP", "App")
    api = projects.create_project(session, "API", "Api")
    add_user(session, "rebecca", app.id)
    browser = as_user(client, "rebecca")

    created = create(browser, "Claude", app.id)

    assert created["token"].startswith("vbp_")
    assert created["item"]["owner"] == "rebecca"
    assert keys(created["item"]) == ["APP"]
    assert reachable(client, created["token"]) == ["APP"]

    refused = browser.post("/api/tokens", json={"name": "Wide", "projectIds": [app.id, api.id]})

    assert refused.status_code == 403
    assert refused.json() == {"error": f"You have no access to project: {api.id}"}
    assert [item["name"] for item in browser.get("/api/tokens").json()["items"]] == ["Claude"]


def test_a_user_sees_and_changes_only_their_own_tokens(
    client: TestClient, session: Session
) -> None:
    app = projects.create_project(session, "APP", "App")
    add_user(session, "rebecca", app.id)
    add_user(session, "jonas", app.id)
    rebecca = as_user(client, "rebecca")
    jonas = as_user(client, "jonas")
    admin = login(client)
    of_admin = create(admin, "Admin agent", app.id)["item"]
    of_jonas = create(jonas, "Jonas agent", app.id)["item"]
    of_rebecca = create(rebecca, "Rebecca agent", app.id)["item"]

    assert [item["id"] for item in rebecca.get("/api/tokens").json()["items"]] == [of_rebecca["id"]]
    for foreign in (of_admin, of_jonas):
        renamed = rebecca.patch(f"/api/tokens/{foreign['id']}", json={"name": "Mine now"})
        revoked = rebecca.post(f"/api/tokens/{foreign['id']}/revoke")
        assert renamed.status_code == 404
        assert revoked.status_code == 404

    renamed = rebecca.patch(f"/api/tokens/{of_rebecca['id']}", json={"name": "Renamed"})
    revoked = rebecca.post(f"/api/tokens/{of_rebecca['id']}/revoke")

    assert renamed.json()["item"]["name"] == "Renamed"
    assert revoked.json()["item"]["revokedAt"]

    everything = {
        item["name"]: item.get("owner") for item in admin.get("/api/tokens").json()["items"]
    }

    assert everything == {"Admin agent": None, "Jonas agent": "jonas", "Renamed": "rebecca"}


def test_a_users_token_never_gets_a_project_the_user_lacks(
    client: TestClient, session: Session
) -> None:
    app = projects.create_project(session, "APP", "App")
    api = projects.create_project(session, "API", "Api")
    add_user(session, "rebecca", app.id)
    browser = as_user(client, "rebecca")
    token_id = create(browser, "Claude", app.id)["item"]["id"]
    admin = login(client)

    by_user = browser.patch(f"/api/tokens/{token_id}", json={"projectIds": [app.id, api.id]})
    by_admin = admin.patch(f"/api/tokens/{token_id}", json={"projectIds": [app.id, api.id]})

    assert by_user.status_code == 403
    assert by_user.json() == {"error": f"You have no access to project: {api.id}"}
    assert by_admin.status_code == 403
    assert by_admin.json() == {"error": f"rebecca has no access to project: {api.id}"}
    [listed] = admin.get("/api/tokens").json()["items"]
    assert keys(listed) == ["APP"]


def test_taking_a_project_from_a_user_takes_it_from_their_tokens(
    client: TestClient, session: Session
) -> None:
    app = projects.create_project(session, "APP", "App")
    api = projects.create_project(session, "API", "Api")
    user_id = add_user(session, "rebecca", app.id, api.id)
    browser = as_user(client, "rebecca")
    created = create(browser, "Claude", app.id, api.id)
    admin = login(client)
    of_admin = create(admin, "Admin agent", app.id, api.id)
    assert reachable(client, created["token"]) == ["API", "APP"]

    admin.patch(f"/api/users/{user_id}", json={"projectIds": [app.id]})

    assert reachable(client, created["token"]) == ["APP"]
    [listed] = browser.get("/api/tokens").json()["items"]
    assert keys(listed) == ["APP"]
    # The admin's own token is nobody's and keeps both projects.
    assert reachable(client, of_admin["token"]) == ["API", "APP"]

    admin.patch(f"/api/users/{user_id}", json={"projectIds": [app.id, api.id]})

    # Giving the project back does not put it on the token again.
    assert reachable(client, created["token"]) == ["APP"]


def test_a_token_reaches_nothing_the_owner_lost_even_if_a_link_is_left(
    client: TestClient, session: Session
) -> None:
    """The check at sign-in is what counts; removing the links only keeps the lists true."""
    app = projects.create_project(session, "APP", "App")
    api = projects.create_project(session, "API", "Api")
    user_id = add_user(session, "rebecca", app.id, api.id)
    created = create(as_user(client, "rebecca"), "Claude", app.id, api.id)

    # Behind the service's back: the assignment goes, the token's link stays.
    session.execute(
        text("delete from user_projects where user_id = :user and project_id = :project"),
        {"user": user_id, "project": api.id},
    )
    session.commit()

    assert reachable(client, created["token"]) == ["APP"]
    authenticated = tokens.authenticate_token(session, created["token"])
    assert authenticated is not None
    assert authenticated.project_ids == [app.id]
    assert list(authenticated.permissions) == [app.id]


def test_a_deactivated_owners_tokens_stop_until_the_owner_is_active_again(
    client: TestClient, session: Session
) -> None:
    app = projects.create_project(session, "APP", "App")
    user_id = add_user(session, "rebecca", app.id)
    created = create(as_user(client, "rebecca"), "Claude", app.id)

    users.update_user(session, user_id, active=False)

    assert reachable(client, created["token"]) == 401
    assert tokens.authenticate_token(session, created["token"]) is None

    users.update_user(session, user_id, active=True)

    assert reachable(client, created["token"]) == ["APP"]


def test_deleting_a_user_deletes_their_tokens(client: TestClient, session: Session) -> None:
    app = projects.create_project(session, "APP", "App")
    user_id = add_user(session, "rebecca", app.id)
    created = create(as_user(client, "rebecca"), "Claude", app.id)
    admin = login(client)
    create(admin, "Admin agent", app.id)

    assert admin.delete(f"/api/users/{user_id}").status_code == 200

    assert reachable(client, created["token"]) == 401
    assert [item["name"] for item in admin.get("/api/tokens").json()["items"]] == ["Admin agent"]


def test_a_password_reset_leaves_the_users_tokens_working(
    client: TestClient, session: Session
) -> None:
    app = projects.create_project(session, "APP", "App")
    user_id = add_user(session, "rebecca", app.id)
    created = create(as_user(client, "rebecca"), "Claude", app.id)

    users.reset_password(session, user_id)

    assert reachable(client, created["token"]) == ["APP"]


def test_an_admin_token_has_no_owner_and_works_as_before(
    client: TestClient, session: Session
) -> None:
    app = projects.create_project(session, "APP", "App")
    admin = login(client)

    created = create(admin, "Admin agent", app.id)

    assert "owner" not in created["item"]
    assert reachable(client, created["token"]) == ["APP"]
    authenticated = tokens.authenticate_token(session, created["token"])
    assert authenticated is not None and authenticated.project_ids == [app.id]


def test_a_token_cannot_manage_tokens_and_a_temporary_password_comes_first(
    client: TestClient, session: Session
) -> None:
    app = projects.create_project(session, "APP", "App")
    token = tokens.create_token(session, "Agent", [app.id]).token
    temporary = users.create_user(session, "jonas", [app.id], "admin").temporary_password

    by_token = TestClient(client.app).get("/api/tokens", headers=bearer(token))
    by_new_user = as_user(client, "jonas", temporary).get("/api/tokens")

    assert by_token.status_code == 403
    assert by_token.json() == {"error": "Sign in to the board for this"}
    assert by_new_user.status_code == 403
    assert by_new_user.json() == {"error": "Set a new password first"}
