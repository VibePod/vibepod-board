"""OAuth sign-in over HTTP: discovery, registration, consent, and the token a sign-in issues."""

import base64
import hashlib
import secrets
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from conftest import login
from fastapi.testclient import TestClient
from httpx import Response
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


def authorize(client: TestClient, client_id: str, challenge: str, state: str = "st4te") -> str:
    """Opens the authorization URL like a browser and returns the id of the stored request."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "redirect_uri": REDIRECT,
        "state": state,
        "resource": f"{PUBLIC_URL}/mcp",
    }
    response = client.get(f"/authorize?{urlencode(params)}", follow_redirects=False)
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{PUBLIC_URL}/oauth/consent?request=")
    return parse_qs(urlparse(location).query)["request"][0]


def redirect_query(response: Response) -> dict[str, str]:
    assert response.status_code == 200, response.text
    url = response.json()["redirectTo"]
    assert url.startswith(REDIRECT + "?")
    return {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}


def exchange(client: TestClient, client_id: str, code: str, verifier: str) -> Response:
    return client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
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
