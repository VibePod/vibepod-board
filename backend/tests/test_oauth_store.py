"""OAuth sign-in storage: registered clients, sign-in requests and their one-time codes."""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import func
from sqlmodel import Session, select

from vibepod_board.errors import BadRequest, NotFound
from vibepod_board.schemas import now
from vibepod_board.services import oauth
from vibepod_board.services.projects import create_project
from vibepod_board.services.tokens import authenticate_token
from vibepod_board.tables import OAuthRequestRow

REDIRECT = "http://127.0.0.1:4000/callback"


def register(session: Session, client_id: str = "client-1", name: str | None = "pi") -> str:
    info = {"client_id": client_id, "redirect_uris": [REDIRECT]}
    if name is not None:
        info["client_name"] = name
    oauth.save_client(session, client_id, info)
    return client_id


def begin(session: Session, client_id: str = "client-1", state: str | None = "state-1") -> str:
    return oauth.create_request(
        session,
        client_id=client_id,
        redirect_uri=REDIRECT,
        redirect_uri_provided_explicitly=True,
        code_challenge="challenge",
        state=state,
        scopes=[],
        resource="http://localhost:3000/mcp",
    )


def query(url: str) -> dict[str, str]:
    assert url.startswith(REDIRECT + "?")
    return {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}


def expire(session: Session, request_id: str) -> None:
    row = session.get(OAuthRequestRow, request_id)
    assert row is not None
    row.expires_at = now() - timedelta(seconds=1)
    session.commit()


def request_count(session: Session) -> int:
    return session.exec(select(func.count()).select_from(OAuthRequestRow)).one()


def test_stores_registered_clients(session: Session) -> None:
    assert oauth.get_client(session, "client-1") is None

    register(session)

    assert oauth.get_client(session, "client-1") == {
        "client_id": "client-1",
        "client_name": "pi",
        "redirect_uris": [REDIRECT],
    }


def test_a_new_request_is_pending_and_names_the_client(session: Session) -> None:
    register(session)
    request_id = begin(session)

    pending = oauth.pending_request(session, request_id)

    assert (pending.id, pending.client_name, pending.redirect_uri) == (request_id, "pi", REDIRECT)
    assert now() < pending.expires_at <= now() + oauth.REQUEST_TTL


def test_a_client_without_a_name_is_shown_as_mcp_client(session: Session) -> None:
    register(session, name=None)

    assert oauth.pending_request(session, begin(session)).client_name == "MCP client"


def test_approval_turns_a_request_into_a_one_time_code(session: Session) -> None:
    app = create_project(session, key="APP", title="App")
    create_project(session, key="OPS", title="Operations")
    register(session)
    request_id = begin(session)

    redirect = query(oauth.approve_request(session, request_id, [app.id]))

    assert sorted(redirect) == ["code", "state"]
    assert redirect["state"] == "state-1"
    with pytest.raises(NotFound):
        oauth.pending_request(session, request_id)

    loaded = oauth.load_code(session, redirect["code"])
    assert loaded is not None
    assert (loaded.client_id, loaded.redirect_uri, loaded.code_challenge) == (
        "client-1",
        REDIRECT,
        "challenge",
    )
    assert now() < loaded.expires_at <= now() + oauth.CODE_TTL
    # Only a hash of the code is stored.
    assert redirect["code"] not in (loaded.code_hash or "")

    created = oauth.redeem_code(session, redirect["code"])
    assert created is not None
    assert created.item.name == "pi (OAuth)"
    assert [project.key for project in created.item.projects] == ["APP"]
    authenticated = authenticate_token(session, created.token)
    assert authenticated is not None
    assert authenticated.project_ids == [app.id]

    assert oauth.redeem_code(session, redirect["code"]) is None
    assert oauth.load_code(session, redirect["code"]) is None
    assert request_count(session) == 0


def test_a_request_without_state_redirects_without_it(session: Session) -> None:
    app = create_project(session, key="APP", title="App")
    register(session)

    redirect = query(oauth.approve_request(session, begin(session, state=None), [app.id]))

    assert sorted(redirect) == ["code"]


def test_denial_redirects_with_an_error_and_forgets_the_request(session: Session) -> None:
    register(session)
    request_id = begin(session)

    redirect = query(oauth.deny_request(session, request_id))

    assert redirect == {"error": "access_denied", "state": "state-1"}
    assert request_count(session) == 0
    with pytest.raises(NotFound):
        oauth.deny_request(session, request_id)


def test_expired_requests_cannot_be_approved_and_are_swept(session: Session) -> None:
    app = create_project(session, key="APP", title="App")
    register(session)
    request_id = begin(session)
    expire(session, request_id)

    with pytest.raises(NotFound):
        oauth.pending_request(session, request_id)
    with pytest.raises(NotFound):
        oauth.approve_request(session, request_id, [app.id])
    with pytest.raises(NotFound):
        oauth.deny_request(session, request_id)

    fresh = begin(session)

    assert [row.id for row in session.exec(select(OAuthRequestRow)).all()] == [fresh]


def test_expired_codes_are_not_redeemed(session: Session) -> None:
    app = create_project(session, key="APP", title="App")
    register(session)
    request_id = begin(session)
    code = query(oauth.approve_request(session, request_id, [app.id]))["code"]
    expire(session, request_id)

    assert oauth.load_code(session, code) is None
    assert oauth.redeem_code(session, code) is None


def test_approval_needs_known_projects_and_keeps_the_request_pending(session: Session) -> None:
    register(session)
    request_id = begin(session)

    with pytest.raises(BadRequest):
        oauth.approve_request(session, request_id, [])
    with pytest.raises(NotFound):
        oauth.approve_request(session, request_id, ["missing"])

    assert oauth.pending_request(session, request_id).id == request_id


def test_unknown_codes_and_requests_are_refused(session: Session) -> None:
    assert oauth.load_code(session, "nope") is None
    assert oauth.redeem_code(session, "nope") is None
    with pytest.raises(NotFound):
        oauth.pending_request(session, "nope")
