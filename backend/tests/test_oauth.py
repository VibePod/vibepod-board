"""OAuth sign-in over HTTP: discovery, registration, consent, and the token a sign-in issues."""

import base64
import hashlib
import secrets
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from conftest import login
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine
from sqlmodel import Session

from vibepod_board import config
from vibepod_board.config import Settings
from vibepod_board.main import create_app
from vibepod_board.schemas import now
from vibepod_board.services import tokens
from vibepod_board.services.projects import create_project
from vibepod_board.tables import OAuthRequestRow

PUBLIC_URL = "http://localhost:3000"
REDIRECT = "http://127.0.0.1:4567/callback"
# Where Claude's hosted apps send the browser back to after a sign-in.
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def register(client: TestClient, redirect: str = REDIRECT) -> Response:
    """Registers the way Pi does: a public native client with a loopback redirect."""
    return client.post(
        "/register",
        json={
            "client_name": "pi",
            "redirect_uris": [redirect],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "application_type": "native",
        },
    )


def authorize(
    client: TestClient,
    client_id: str,
    challenge: str,
    state: str = "st4te",
    redirect: str = REDIRECT,
) -> str:
    """Opens the authorization URL like a browser and returns the id of the stored request."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "redirect_uri": redirect,
        "state": state,
        "resource": f"{PUBLIC_URL}/mcp",
    }
    response = client.get(f"/authorize?{urlencode(params)}", follow_redirects=False)
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{PUBLIC_URL}/oauth/consent?request=")
    return parse_qs(urlparse(location).query)["request"][0]


def redirect_query(response: Response, redirect: str = REDIRECT) -> dict[str, str]:
    assert response.status_code == 200, response.text
    url = response.json()["redirectTo"]
    assert url.startswith(redirect + "?")
    return {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}


def exchange(
    client: TestClient, client_id: str, code: str, verifier: str, redirect: str = REDIRECT
) -> Response:
    return client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )


def begin(client: TestClient) -> tuple[str, str, str]:
    """A registered client with an authorization request waiting for approval."""
    client_id = register(client).json()["client_id"]
    verifier, challenge = pkce()
    return client_id, verifier, authorize(client, client_id, challenge)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def projects(session: Session) -> dict[str, str]:
    app = create_project(session, key="APP", title="App")
    ops = create_project(session, key="OPS", title="Operations")
    return {"app": app.id, "ops": ops.id}


def test_unauthenticated_mcp_calls_point_to_the_authorization_server(client: TestClient) -> None:
    refused = client.post("/mcp", json=TOOLS_LIST, headers=MCP_HEADERS)
    assert refused.status_code == 401
    metadata_url = f"{PUBLIC_URL}/.well-known/oauth-protected-resource/mcp"
    assert f'resource_metadata="{metadata_url}"' in refused.headers["www-authenticate"]

    resource = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert resource["resource"] == f"{PUBLIC_URL}/mcp"
    assert resource["authorization_servers"] == [f"{PUBLIC_URL}/"]

    server = client.get("/.well-known/oauth-authorization-server").json()
    assert server["issuer"] == f"{PUBLIC_URL}/"
    assert server["authorization_endpoint"] == f"{PUBLIC_URL}/authorize"
    assert server["token_endpoint"] == f"{PUBLIC_URL}/token"
    assert server["registration_endpoint"] == f"{PUBLIC_URL}/register"
    assert server["code_challenge_methods_supported"] == ["S256"]
    assert "revocation_endpoint" not in server


def test_sign_in_issues_a_token_scoped_to_the_approved_projects(
    client: TestClient, projects: dict[str, str]
) -> None:
    client_id, verifier, request_id = begin(client)

    login(client)
    waiting = client.get(f"/api/oauth/requests/{request_id}")
    assert waiting.status_code == 200
    assert waiting.json()["item"]["clientName"] == "pi"
    assert waiting.json()["item"]["redirectUri"] == REDIRECT

    approved = redirect_query(
        client.post(
            f"/api/oauth/requests/{request_id}/approve", json={"projectIds": [projects["app"]]}
        )
    )
    assert approved["state"] == "st4te"
    assert client.get(f"/api/oauth/requests/{request_id}").status_code == 404

    issued = exchange(client, client_id, approved["code"], verifier)
    assert issued.status_code == 200, issued.text
    body: dict[str, Any] = issued.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"].startswith("vbp_")
    assert not body.get("refresh_token")
    assert not body.get("expires_in")

    listed = client.get("/api/tokens").json()["items"]
    assert [(item["name"], [p["key"] for p in item["projects"]]) for item in listed] == [
        ("pi (OAuth)", ["APP"])
    ]

    # Without the admin cookie, the token alone decides what is reachable.
    client.cookies.clear()
    headers = bearer(body["access_token"])
    assert (
        client.get("/api/board", params={"projectId": projects["app"]}, headers=headers).status_code
        == 200
    )
    assert (
        client.get("/api/board", params={"projectId": projects["ops"]}, headers=headers).status_code
        == 403
    )
    tools = client.post("/mcp", json=TOOLS_LIST, headers={**MCP_HEADERS, **headers})
    assert tools.status_code == 200, tools.text
    assert len(tools.json()["result"]["tools"]) == 60


def test_a_code_works_once_and_only_with_the_right_verifier(
    client: TestClient, projects: dict[str, str]
) -> None:
    client_id, verifier, request_id = begin(client)
    login(client)
    code = redirect_query(
        client.post(
            f"/api/oauth/requests/{request_id}/approve", json={"projectIds": [projects["app"]]}
        )
    )["code"]

    wrong = exchange(client, client_id, code, "not-the-verifier-" + "x" * 40)
    assert wrong.status_code in (400, 401)
    assert wrong.json()["error"] == "invalid_grant"

    assert exchange(client, client_id, code, verifier).status_code == 200
    again = exchange(client, client_id, code, verifier)
    assert again.status_code in (400, 401)
    assert again.json()["error"] == "invalid_grant"
    assert len(client.get("/api/tokens").json()["items"]) == 1


def test_denying_sends_the_client_an_error_and_issues_nothing(client: TestClient) -> None:
    _client_id, _verifier, request_id = begin(client)
    login(client)

    denied = redirect_query(client.post(f"/api/oauth/requests/{request_id}/deny"))

    assert denied == {"error": "access_denied", "state": "st4te"}
    assert client.get(f"/api/oauth/requests/{request_id}").status_code == 404
    assert client.get("/api/tokens").json()["items"] == []


def test_only_the_admin_decides_on_a_request(
    client: TestClient, session: Session, projects: dict[str, str]
) -> None:
    _client_id, _verifier, request_id = begin(client)
    approval = {"projectIds": [projects["app"]]}
    paths = [
        ("get", f"/api/oauth/requests/{request_id}", None),
        ("post", f"/api/oauth/requests/{request_id}/approve", approval),
        ("post", f"/api/oauth/requests/{request_id}/deny", None),
    ]

    for method, path, body in paths:
        assert client.request(method, path, json=body).status_code == 401, path

    token = tokens.create_token(session, "Agent", [projects["app"]]).token
    for method, path, body in paths:
        refused = client.request(method, path, json=body, headers=bearer(token))
        assert refused.status_code == 403, path

    login(client)
    assert client.get(f"/api/oauth/requests/{request_id}").status_code == 200


def test_approval_needs_a_live_request_and_known_projects(
    client: TestClient, session: Session, projects: dict[str, str]
) -> None:
    _client_id, _verifier, request_id = begin(client)
    login(client)
    approve = f"/api/oauth/requests/{request_id}/approve"

    assert client.post(approve, json={"projectIds": []}).status_code == 400
    assert client.post(approve, json={"projectIds": ["missing"]}).status_code == 404
    assert (
        client.post("/api/oauth/requests/nope/approve", json={"projectIds": ["x"]}).status_code
        == 404
    )

    row = session.get(OAuthRequestRow, request_id)
    assert row is not None
    row.expires_at = now() - timedelta(seconds=1)
    session.commit()

    expired = client.post(approve, json={"projectIds": [projects["app"]]})
    assert expired.status_code == 404
    assert expired.json() == {"error": "Sign-in request not found or expired"}


def test_registration_accepts_only_loopback_redirects(client: TestClient) -> None:
    for redirect in ("http://127.0.0.1:4567/callback", "http://localhost:8765/callback"):
        assert register(client, redirect).status_code == 201, redirect

    for redirect in ("https://evil.example/callback", "http://192.168.1.20:4567/callback"):
        refused = register(client, redirect)
        assert refused.status_code == 400, redirect
        assert refused.json()["error"] == "invalid_redirect_uri"


def test_authorize_refuses_an_unregistered_redirect(client: TestClient) -> None:
    client_id = register(client).json()["client_id"]
    _verifier, challenge = pkce()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "redirect_uri": "http://127.0.0.1:9999/elsewhere",
    }

    response = client.get(f"/authorize?{urlencode(params)}", follow_redirects=False)

    assert response.status_code == 400
    assert "location" not in response.headers


def test_unknown_well_known_paths_are_not_answered_by_the_client(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("<title>board client</title>")
    built = replace(settings, public_dir=tmp_path)
    with TestClient(create_app(built, run_migrations=False)) as with_client:
        assert with_client.get("/projects").text == "<title>board client</title>"
        assert with_client.get("/.well-known/openid-configuration").status_code == 404
        assert with_client.get("/.well-known/oauth-authorization-server").status_code == 200


def test_public_url_defaults_to_localhost_and_must_be_https_or_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    assert config.public_url_from_env() == "http://localhost:3000"

    monkeypatch.setenv("PORT", "8080")
    assert config.public_url_from_env() == "http://localhost:8080"

    for accepted in ("https://board.example.com", "http://127.0.0.1:3000", "http://[::1]:3000"):
        monkeypatch.setenv("PUBLIC_URL", accepted + "/")
        assert config.public_url_from_env() == accepted

    for refused in (
        "http://vibepod-board:3000",
        "ftp://localhost",
        "https://board.example.com/app",
    ):
        monkeypatch.setenv("PUBLIC_URL", refused)
        with pytest.raises(RuntimeError, match="PUBLIC_URL"):
            config.public_url_from_env()


def test_consent_page_is_served_and_cannot_be_framed(client: TestClient) -> None:
    page = client.get("/oauth/consent", params={"request": "anything"})

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert page.headers["cache-control"] == "no-store"
    assert "/api/oauth/requests/" in page.text
    assert "/oauth/consent" not in client.get("/openapi.json").json()["paths"]


def test_sign_in_issues_a_token_with_the_approved_permissions(
    client: TestClient, projects: dict[str, str]
) -> None:
    client_id, verifier, request_id = begin(client)
    login(client)

    approved = redirect_query(
        client.post(
            f"/api/oauth/requests/{request_id}/approve",
            json={
                "projectIds": [projects["app"]],
                "permissions": {"tasks": "read", "documents": "write"},
            },
        )
    )
    issued = exchange(client, client_id, approved["code"], verifier)
    assert issued.status_code == 200, issued.text

    permissions = client.get("/api/tokens").json()["items"][0]["projects"][0]["permissions"]
    assert permissions == {
        "tasks": "read",
        "board": "none",
        "documents": "write",
        "github": "none",
        "automation": "none",
        "project": "none",
    }

    token = TestClient(client.app)
    headers = bearer(issued.json()["access_token"])
    assert token.get("/api/ideas", headers=headers).status_code == 200
    refused = token.post("/api/ideas", headers=headers, json={"title": "New"})
    assert refused.status_code == 403
    assert refused.json() == {"error": "This token has no write access to Tasks"}


def test_sign_in_without_chosen_permissions_issues_a_token_with_full_access(
    client: TestClient, projects: dict[str, str]
) -> None:
    client_id, verifier, request_id = begin(client)
    login(client)

    approved = redirect_query(
        client.post(
            f"/api/oauth/requests/{request_id}/approve", json={"projectIds": [projects["app"]]}
        )
    )
    assert exchange(client, client_id, approved["code"], verifier).status_code == 200

    permissions = client.get("/api/tokens").json()["items"][0]["projects"][0]["permissions"]
    assert set(permissions.values()) == {"write"}


def test_sign_in_issues_a_token_with_its_own_permissions_in_each_project(
    client: TestClient, projects: dict[str, str]
) -> None:
    client_id, verifier, request_id = begin(client)
    login(client)

    approved = redirect_query(
        client.post(
            f"/api/oauth/requests/{request_id}/approve",
            json={
                "projectIds": [projects["app"], projects["ops"]],
                "permissions": {"tasks": "read"},
                "projectPermissions": {projects["ops"]: {"tasks": "write"}},
            },
        )
    )
    issued = exchange(client, client_id, approved["code"], verifier)
    assert issued.status_code == 200, issued.text

    mapped = client.get("/api/tokens").json()["items"][0]["projects"]
    assert {project["key"]: project["permissions"]["tasks"] for project in mapped} == {
        "APP": "read",
        "OPS": "write",
    }

    token = TestClient(client.app)
    headers = bearer(issued.json()["access_token"])
    in_ops = token.post(
        "/api/ideas", headers=headers, json={"projectId": projects["ops"], "title": "New"}
    )
    in_app = token.post(
        "/api/ideas", headers=headers, json={"projectId": projects["app"], "title": "New"}
    )
    assert in_ops.status_code == 201
    assert in_app.status_code == 403


def test_approval_refuses_unknown_permissions_and_keeps_the_request_pending(
    client: TestClient, projects: dict[str, str]
) -> None:
    _client_id, _verifier, request_id = begin(client)
    login(client)

    refused = client.post(
        f"/api/oauth/requests/{request_id}/approve",
        json={"projectIds": [projects["app"]], "permissions": {"billing": "read"}},
    )

    assert refused.status_code == 400
    assert refused.json() == {"error": "Unknown permission area: billing"}
    assert client.get(f"/api/oauth/requests/{request_id}").status_code == 200


# Redirects outside the machine, for hosted clients such as Claude on the web


@pytest.fixture
def hosted(db: Engine, settings: Settings) -> Iterator[TestClient]:
    """A board configured to accept Claude's hosted callback next to loopback redirects."""
    configured = replace(settings, oauth_redirect_uris=(CLAUDE_CALLBACK,))
    with TestClient(create_app(configured, run_migrations=False)) as test_client:
        yield test_client


def register_hosted(client: TestClient, redirect: str = CLAUDE_CALLBACK) -> Response:
    """Registers the way a hosted client does: a public web client with an https redirect."""
    return client.post(
        "/register",
        json={
            "client_name": "Claude",
            "redirect_uris": [redirect],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )


def test_a_redirect_outside_the_machine_is_refused_unless_the_board_is_configured_for_it(
    client: TestClient,
) -> None:
    refused = register_hosted(client)

    assert refused.status_code == 400
    assert refused.json()["error"] == "invalid_redirect_uri"
    assert "OAUTH_REDIRECT_URIS" in refused.json()["error_description"]


def test_a_configured_redirect_outside_the_machine_signs_in_like_a_local_one(
    hosted: TestClient, projects: dict[str, str]
) -> None:
    registered = register_hosted(hosted)
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]
    verifier, challenge = pkce()
    request_id = authorize(hosted, client_id, challenge, redirect=CLAUDE_CALLBACK)

    login(hosted)
    waiting = hosted.get(f"/api/oauth/requests/{request_id}").json()["item"]
    assert waiting["clientName"] == "Claude"
    assert waiting["redirectUri"] == CLAUDE_CALLBACK

    approved = redirect_query(
        hosted.post(
            f"/api/oauth/requests/{request_id}/approve", json={"projectIds": [projects["app"]]}
        ),
        CLAUDE_CALLBACK,
    )
    assert approved["state"] == "st4te"

    issued = exchange(hosted, client_id, approved["code"], verifier, CLAUDE_CALLBACK)
    assert issued.status_code == 200, issued.text
    assert issued.json()["access_token"].startswith("vbp_")

    token = TestClient(hosted.app)
    listed = token.get("/api/projects", headers=bearer(issued.json()["access_token"]))
    assert [project["key"] for project in listed.json()["items"]] == ["APP"]


def test_a_configured_board_accepts_only_the_listed_addresses_and_loopback(
    hosted: TestClient,
) -> None:
    assert register(hosted).status_code == 201

    for redirect in (
        "https://evil.example/callback",
        "https://claude.ai/api/mcp/other",
        "https://claude.ai.evil.example/api/mcp/auth_callback",
        "http://claude.ai/api/mcp/auth_callback",
        CLAUDE_CALLBACK + "?next=https://evil.example",
    ):
        refused = register_hosted(hosted, redirect)
        assert refused.status_code == 400, redirect
        assert refused.json()["error"] == "invalid_redirect_uri"


def test_a_client_cannot_add_an_unlisted_redirect_next_to_a_listed_one(hosted: TestClient) -> None:
    refused = hosted.post(
        "/register",
        json={
            "client_name": "Claude",
            "redirect_uris": [CLAUDE_CALLBACK, "https://evil.example/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )

    assert refused.status_code == 400
    assert refused.json()["error"] == "invalid_redirect_uri"


def test_extra_redirect_addresses_come_from_the_environment_and_must_be_complete_https_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAUTH_REDIRECT_URIS", raising=False)
    assert config.oauth_redirect_uris_from_env() == ()

    monkeypatch.setenv("OAUTH_REDIRECT_URIS", f" {CLAUDE_CALLBACK} , https://b.example/cb ,")
    assert config.oauth_redirect_uris_from_env() == (CLAUDE_CALLBACK, "https://b.example/cb")

    for refused in (
        "http://claude.ai/api/mcp/auth_callback",
        "claude.ai/api/mcp/auth_callback",
        "https://*.example.com/callback",
        "https://b.example/cb#fragment",
        "https:///callback",
    ):
        monkeypatch.setenv("OAUTH_REDIRECT_URIS", refused)
        with pytest.raises(RuntimeError, match="OAUTH_REDIRECT_URIS"):
            config.oauth_redirect_uris_from_env()


@pytest.mark.parametrize(
    ("configured", "registered"),
    [
        ("https://client.example", "https://client.example"),
        ("https://client.example", "https://client.example/"),
        ("https://client.example/", "https://client.example"),
    ],
)
def test_a_configured_address_without_a_path_signs_in_with_or_without_the_slash(
    db: Engine, settings: Settings, projects: dict[str, str], configured: str, registered: str
) -> None:
    """`https://client.example` and `https://client.example/` are one address, however
    the operator lists it and however the client writes it."""
    board = replace(settings, oauth_redirect_uris=(configured,))
    with TestClient(create_app(board, run_migrations=False)) as hosted:
        accepted = register_hosted(hosted, registered)
        assert accepted.status_code == 201, accepted.text
        client_id = accepted.json()["client_id"]
        verifier, challenge = pkce()
        request_id = authorize(hosted, client_id, challenge, redirect=registered)

        login(hosted)
        approved = redirect_query(
            hosted.post(
                f"/api/oauth/requests/{request_id}/approve",
                json={"projectIds": [projects["app"]]},
            ),
            # The browser is sent to the root path, which is where a request for an
            # address without a path goes anyway.
            "https://client.example/",
        )
        issued = exchange(hosted, client_id, approved["code"], verifier, registered)

    assert issued.status_code == 200, issued.text
    assert issued.json()["access_token"].startswith("vbp_")
