"""Board users, managed by the admin. Every change that takes access away also ends the
user's sessions."""

from fastapi import APIRouter, status

from vibepod_board.api.deps import SettingsDep
from vibepod_board.api.models import UserCreate, UserUpdate
from vibepod_board.api.responses import Item, Items
from vibepod_board.auth import AdminDep, SessionsDep
from vibepod_board.db import SessionDep
from vibepod_board.schemas import DeletedUser, UserCredentials, UserSummary
from vibepod_board.services import users

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
def list_users(session: SessionDep, _admin: AdminDep) -> Items[UserSummary]:
    """Every user with their projects, when they last signed in (`lastLoginAt`), and
    whether they still have the temporary password (`mustChangePassword`)."""
    return Items(items=users.list_users(session))


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate, session: SessionDep, _admin: AdminDep, settings: SettingsDep
) -> UserCredentials:
    """Creates a user. `temporaryPassword` is generated and returned this once; the user
    must replace it right after signing in."""
    return users.create_user(session, body.username, body.project_ids, settings.admin_username)


@router.patch("/{user_id}")
def update_user(
    user_id: str,
    body: UserUpdate,
    session: SessionDep,
    _admin: AdminDep,
    sessions: SessionsDep,
) -> Item[UserSummary]:
    """`projectIds` replaces the user's projects; `active: false` signs the user out and
    blocks sign-in until it is set to true again."""
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    item = users.update_user(session, user_id, **changes)
    if not item.active:
        sessions.end_user_sessions(user_id)
    return Item(item=item)


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: str, session: SessionDep, _admin: AdminDep, sessions: SessionsDep
) -> UserCredentials:
    """Replaces the password with a new temporary one, returned this once, and signs the
    user out. The old password stops working at once."""
    credentials = users.reset_password(session, user_id)
    sessions.end_user_sessions(user_id)
    return credentials


@router.delete("/{user_id}")
def delete_user(
    user_id: str, session: SessionDep, _admin: AdminDep, sessions: SessionsDep
) -> DeletedUser:
    """Deletes the user for good. Tasks and comments they wrote stay."""
    deleted = users.delete_user(session, user_id)
    sessions.end_user_sessions(user_id)
    return deleted
