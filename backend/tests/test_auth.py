import re

from vibepod_board.auth import AdminSessionManager, parse_bearer_token
from vibepod_board.services.tokens import create_raw_token, hash_token


def test_creates_and_validates_admin_sessions() -> None:
    sessions = AdminSessionManager("admin", "secret")

    assert sessions.login("admin", "wrong") is None
    session = sessions.login("admin", "secret")
    assert session is not None
    assert session.username == "admin"
    assert "vibepod_session=" in session.cookie
    authenticated = sessions.authenticate_cookie(session.cookie)
    assert authenticated is not None and authenticated.username == "admin"

    sessions.logout(session.id)
    assert sessions.authenticate_cookie(session.cookie) is None


def test_generates_hashable_bearer_tokens() -> None:
    token = create_raw_token()

    assert re.fullmatch(r"vbp_[A-Za-z0-9_-]{43}", token)
    assert len(hash_token(token)) == 64
    assert hash_token(token) == hash_token(token)


def test_parses_bearer_tokens() -> None:
    assert parse_bearer_token("Bearer vbp_abc") == "vbp_abc"
    assert parse_bearer_token("Basic abc") is None
    assert parse_bearer_token(None) is None
