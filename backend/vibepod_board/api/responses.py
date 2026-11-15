from pydantic import BaseModel

from vibepod_board.schemas import ApiModel, BoardColumns


class Item[T](ApiModel):
    item: T


class Items[T](ApiModel):
    items: list[T]


class ItemsPage[T](ApiModel):
    items: list[T]
    # Present when more rows remain; pass it back as `cursor`.
    next_cursor: str | None = None


class Columns(ApiModel):
    columns: BoardColumns


class AuthState(ApiModel):
    authenticated: bool
    username: str | None = None


class Redirect(ApiModel):
    """Where the browser has to go next."""

    redirect_to: str


class SyncResult(ApiModel):
    mode: str
    card: BaseModel
