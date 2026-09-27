from pydantic import BaseModel

from vibepod_board.schemas import ApiModel, BoardColumns


class Item[T](ApiModel):
    item: T


class Items[T](ApiModel):
    items: list[T]


class Columns(ApiModel):
    columns: BoardColumns


class AuthState(ApiModel):
    authenticated: bool
    username: str | None = None


class SyncResult(ApiModel):
    mode: str
    card: BaseModel
