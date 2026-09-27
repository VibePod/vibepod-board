"""Port of tests/token-store.test.ts."""

from sqlalchemy import text
from sqlmodel import Session

from vibepod_board.services.projects import create_project
from vibepod_board.services.tokens import (
    AuthenticatedToken,
    authenticate_token,
    create_token,
    revoke_token,
    update_token,
)


def test_creates_tokens_mapped_to_projects_and_stores_only_a_hash(session: Session) -> None:
    project = create_project(session, key="APP", title="App")

    created = create_token(session, name="Codex", project_ids=[project.id])

    assert created.token.startswith("vbp_")
    assert created.item.name == "Codex"
    assert [ref.model_dump(mode="json") for ref in created.item.projects] == [
        {"id": project.id, "key": "APP", "title": "App"}
    ]

    raw_rows = session.execute(text("select token_hash from api_tokens")).all()
    assert raw_rows[0].token_hash != created.token

    authenticated = authenticate_token(session, created.token)
    assert authenticated == AuthenticatedToken(token_id=created.item.id, project_ids=[project.id])


def test_updates_project_mappings_and_rejects_revoked_tokens(session: Session) -> None:
    app = create_project(session, key="APP", title="App")
    api = create_project(session, key="API", title="API")
    created = create_token(session, name="Agent", project_ids=[app.id])

    updated = update_token(session, created.item.id, name="Agent updated", project_ids=[api.id])
    assert updated.name == "Agent updated"
    assert [project.id for project in updated.projects] == [api.id]

    revoke_token(session, created.item.id)
    assert authenticate_token(session, created.token) is None
