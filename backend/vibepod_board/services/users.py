"""Board users: accounts the admin creates, each with the projects it may work in. Only a
scrypt hash of a password is stored; a temporary password is shown once."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete
from sqlmodel import Session, col, select

from vibepod_board import passwords
from vibepod_board.errors import BadRequest, Conflict, Forbidden, NotFound
from vibepod_board.schemas import DeletedUser, UserCredentials, UserSummary, now
from vibepod_board.services.common import new_id, normalize_list, require_projects, transactional
from vibepod_board.tables import ApiTokenProjectRow, ApiTokenRow, UserProjectRow, UserRow

USERNAME_PATTERN = re.compile(r"[a-z0-9._-]{3,64}")


@dataclass(frozen=True)
class SignedInUser:
    """An active user as a request sees them."""

    id: str
    username: str
    must_change_password: bool
    project_ids: tuple[str, ...]


def normalize_username(username: str) -> str:
    return username.strip().lower()


def _project_ids(session: Session, user_id: str) -> list[str]:
    return list(
        session.exec(
            select(UserProjectRow.project_id)
            .where(UserProjectRow.user_id == user_id)
            .order_by(col(UserProjectRow.project_id))
        )
    )


def _summary(session: Session, row: UserRow) -> UserSummary:
    return UserSummary(
        id=row.id,
        username=row.username,
        active=row.active,
        must_change_password=row.must_change_password,
        project_ids=_project_ids(session, row.id),
        created_at=row.created_at,
        last_login_at=row.last_login_at,
        password_changed_at=row.password_changed_at,
    )


def _signed_in(session: Session, row: UserRow) -> SignedInUser:
    return SignedInUser(
        id=row.id,
        username=row.username,
        must_change_password=row.must_change_password,
        project_ids=tuple(_project_ids(session, row.id)),
    )


def _find(session: Session, *where) -> UserRow | None:
    """Reads the row again even when the session has seen it: another request may have
    changed it since."""
    return session.exec(
        select(UserRow).where(*where).execution_options(populate_existing=True)
    ).first()


def _require_user(session: Session, user_id: str) -> UserRow:
    row = _find(session, UserRow.id == user_id)
    if row is None:
        raise NotFound(f"User not found: {user_id}")
    return row


def _replace_projects(session: Session, user_id: str, project_ids: Sequence[str]) -> None:
    """Assigns exactly these projects. A project that goes is also taken from the user's
    tokens, so that their lists show what they still reach; `authenticate_token` refuses
    the project either way."""
    require_projects(session, project_ids)
    kept = normalize_list(project_ids)
    session.execute(delete(UserProjectRow).where(col(UserProjectRow.user_id) == user_id))
    session.add_all(UserProjectRow(user_id=user_id, project_id=project_id) for project_id in kept)
    own_tokens = select(ApiTokenRow.id).where(ApiTokenRow.owner_user_id == user_id)
    session.execute(
        delete(ApiTokenProjectRow).where(
            col(ApiTokenProjectRow.token_id).in_(own_tokens),
            col(ApiTokenProjectRow.project_id).not_in(kept),
        )
    )
    session.flush()


def list_users(session: Session) -> list[UserSummary]:
    rows = session.exec(select(UserRow).order_by(col(UserRow.username))).all()
    return [_summary(session, row) for row in rows]


@transactional
def create_user(
    session: Session, username: str, project_ids: Sequence[str], admin_username: str
) -> UserCredentials:
    """A new user with a generated temporary password. The name is lower-cased; the admin's
    name cannot be taken."""
    name = normalize_username(username)
    if not USERNAME_PATTERN.fullmatch(name):
        raise BadRequest("Username must be 3 to 64 characters: a-z, 0-9, dot, underscore or hyphen")
    if name == normalize_username(admin_username) or _find(session, UserRow.username == name):
        raise Conflict(f"Username is taken: {name}")
    password = passwords.generate_temporary_password()
    row = UserRow(
        id=new_id(),
        username=name,
        password_hash=passwords.hash_password(password),
        must_change_password=True,
        active=True,
        created_at=now(),
    )
    session.add(row)
    session.flush()
    _replace_projects(session, row.id, project_ids)
    return UserCredentials(item=_summary(session, row), temporary_password=password)


@transactional
def update_user(
    session: Session,
    user_id: str,
    project_ids: Sequence[str] | None = None,
    active: bool | None = None,
) -> UserSummary:
    """Replaces the user's projects and/or switches the user on or off. What is not given
    stays."""
    row = _require_user(session, user_id)
    if project_ids is not None:
        _replace_projects(session, row.id, project_ids)
    if active is not None:
        row.active = active
    session.flush()
    return _summary(session, row)


@transactional
def reset_password(session: Session, user_id: str) -> UserCredentials:
    """Replaces the password with a new temporary one, which the user must change again."""
    row = _require_user(session, user_id)
    password = passwords.generate_temporary_password()
    row.password_hash = passwords.hash_password(password)
    row.must_change_password = True
    session.flush()
    return UserCredentials(item=_summary(session, row), temporary_password=password)


@transactional
def delete_user(session: Session, user_id: str) -> DeletedUser:
    """Removes the user and the project assignments. What the user wrote stays; it carries
    the name as text."""
    row = _require_user(session, user_id)
    deleted = DeletedUser(id=row.id, username=row.username)
    session.delete(row)
    session.flush()
    return deleted


@transactional
def authenticate(session: Session, username: str, password: str) -> SignedInUser | None:
    """The active user these credentials belong to, stamped with `last_login_at`. An
    unknown name costs one hash computation too, so the time does not tell whether a name
    exists."""
    row = _find(session, UserRow.username == normalize_username(username))
    if row is None or not row.active:
        passwords.verify_password(password, passwords.DUMMY_HASH)
        return None
    if not passwords.verify_password(password, row.password_hash):
        return None
    row.last_login_at = now()
    session.flush()
    return _signed_in(session, row)


def signed_in(session: Session, user_id: str) -> SignedInUser | None:
    """The user behind a session, or None once they were deactivated or deleted."""
    row = _find(session, UserRow.id == user_id)
    if row is None or not row.active:
        return None
    return _signed_in(session, row)


@transactional
def change_password(session: Session, user_id: str, current: str, new: str) -> None:
    """Sets the password the user chose and ends the duty to change it."""
    row = _require_user(session, user_id)
    if not passwords.verify_password(current, row.password_hash):
        raise Forbidden("Current password is wrong")
    if not passwords.MIN_LENGTH <= len(new) <= passwords.MAX_LENGTH:
        raise BadRequest(
            f"Password must be {passwords.MIN_LENGTH} to {passwords.MAX_LENGTH} characters"
        )
    if new == current:
        raise BadRequest("The new password must differ from the current one")
    row.password_hash = passwords.hash_password(new)
    row.must_change_password = False
    row.password_changed_at = now()
    session.flush()
