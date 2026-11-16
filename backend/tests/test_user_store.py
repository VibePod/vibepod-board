"""Users at the service level: accounts, passwords and assigned projects."""

import re

import pytest
from sqlalchemy import text
from sqlmodel import Session

from vibepod_board import passwords
from vibepod_board.errors import BadRequest, Conflict, Forbidden, NotFound
from vibepod_board.services import users
from vibepod_board.services.projects import create_project
from vibepod_board.tables import UserRow

OWN_PASSWORD = "a password of her own"


def new_user(session: Session, username: str = "rebecca", *project_ids: str):
    return users.create_user(session, username, list(project_ids), "admin")


def test_creates_a_user_with_a_temporary_password_and_stores_only_a_hash(
    session: Session,
) -> None:
    project = create_project(session, "APP", "App")

    created = new_user(session, "Rebecca", project.id)

    assert re.fullmatch(r"[A-Za-z0-9_-]{16}", created.temporary_password)
    assert created.item.username == "rebecca"
    assert created.item.active is True
    assert created.item.must_change_password is True
    assert created.item.project_ids == [project.id]
    assert created.item.last_login_at is None
    assert created.item.password_changed_at is None
    row = session.get(UserRow, created.item.id)
    assert row is not None
    assert created.temporary_password not in row.password_hash
    assert passwords.verify_password(created.temporary_password, row.password_hash)


@pytest.mark.parametrize("username", ["ab", "has space", "ümlaut", "a" * 65, ""])
def test_refuses_a_malformed_username(session: Session, username: str) -> None:
    with pytest.raises(BadRequest, match="Username must be 3 to 64 characters"):
        new_user(session, username)


def test_refuses_a_taken_username_and_the_admins(session: Session) -> None:
    new_user(session, "rebecca")

    with pytest.raises(Conflict, match="Username is taken: rebecca"):
        new_user(session, "REBECCA")
    with pytest.raises(Conflict, match="Username is taken: admin"):
        new_user(session, "Admin")


def test_refuses_a_project_that_does_not_exist(session: Session) -> None:
    with pytest.raises(NotFound, match="Project not found: missing"):
        new_user(session, "rebecca", "missing")
    assert users.list_users(session) == []


def test_lists_users_by_name(session: Session) -> None:
    new_user(session, "rebecca")
    new_user(session, "jonas")

    assert [user.username for user in users.list_users(session)] == ["jonas", "rebecca"]


def test_signs_in_with_the_right_password_only(session: Session) -> None:
    project = create_project(session, "APP", "App")
    created = new_user(session, "rebecca", project.id)

    assert users.authenticate(session, "rebecca", "wrong") is None
    assert users.authenticate(session, "nobody", created.temporary_password) is None
    assert users.list_users(session)[0].last_login_at is None

    signed_in = users.authenticate(session, " Rebecca ", created.temporary_password)

    assert signed_in == users.SignedInUser(
        id=created.item.id,
        username="rebecca",
        must_change_password=True,
        project_ids=(project.id,),
    )
    assert users.list_users(session)[0].last_login_at is not None


def test_a_password_change_follows_the_rules_and_clears_the_flag(session: Session) -> None:
    created = new_user(session)
    user_id, temporary = created.item.id, created.temporary_password

    with pytest.raises(Forbidden, match="Current password is wrong"):
        users.change_password(session, user_id, "wrong", OWN_PASSWORD)
    with pytest.raises(BadRequest, match="Password must be 12 to 128 characters"):
        users.change_password(session, user_id, temporary, "short")
    with pytest.raises(BadRequest, match="Password must be 12 to 128 characters"):
        users.change_password(session, user_id, temporary, "x" * 129)
    with pytest.raises(BadRequest, match="must differ from the current one"):
        users.change_password(session, user_id, temporary, temporary)
    assert users.list_users(session)[0].must_change_password is True

    users.change_password(session, user_id, temporary, OWN_PASSWORD)

    [user] = users.list_users(session)
    assert user.must_change_password is False
    assert user.password_changed_at is not None
    assert users.authenticate(session, "rebecca", temporary) is None
    assert users.authenticate(session, "rebecca", OWN_PASSWORD) is not None


def test_a_reset_gives_a_new_temporary_password_and_sets_the_flag_again(
    session: Session,
) -> None:
    created = new_user(session)
    users.change_password(session, created.item.id, created.temporary_password, OWN_PASSWORD)

    reset = users.reset_password(session, created.item.id)

    assert reset.temporary_password != created.temporary_password
    assert reset.item.must_change_password is True
    assert users.authenticate(session, "rebecca", OWN_PASSWORD) is None
    assert users.authenticate(session, "rebecca", reset.temporary_password) is not None


def test_changes_the_projects_of_a_user(session: Session) -> None:
    app = create_project(session, "APP", "App")
    api = create_project(session, "API", "Api")
    created = new_user(session, "rebecca", app.id)

    updated = users.update_user(session, created.item.id, project_ids=[api.id, api.id])

    assert updated.project_ids == [api.id]
    signed_in = users.signed_in(session, created.item.id)
    assert signed_in is not None and signed_in.project_ids == (api.id,)
    with pytest.raises(NotFound, match="Project not found: missing"):
        users.update_user(session, created.item.id, project_ids=["missing"])


def test_a_deactivated_user_cannot_sign_in_until_activated(session: Session) -> None:
    created = new_user(session)

    off = users.update_user(session, created.item.id, active=False)

    assert off.active is False
    assert users.signed_in(session, created.item.id) is None
    assert users.authenticate(session, "rebecca", created.temporary_password) is None

    users.update_user(session, created.item.id, active=True)

    assert users.signed_in(session, created.item.id) is not None
    assert users.authenticate(session, "rebecca", created.temporary_password) is not None


def test_deletes_a_user_with_the_project_assignments(session: Session) -> None:
    project = create_project(session, "APP", "App")
    created = new_user(session, "rebecca", project.id)

    deleted = users.delete_user(session, created.item.id)

    assert (deleted.id, deleted.username) == (created.item.id, "rebecca")
    assert users.list_users(session) == []
    assert users.signed_in(session, created.item.id) is None
    assert session.execute(text("select count(*) from user_projects")).scalar_one() == 0
    with pytest.raises(NotFound, match="User not found"):
        users.delete_user(session, created.item.id)
    with pytest.raises(NotFound, match="User not found"):
        users.update_user(session, created.item.id, active=False)
