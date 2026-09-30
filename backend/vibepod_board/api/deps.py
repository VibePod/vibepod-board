from typing import Annotated

from fastapi import Depends, Request

from vibepod_board.config import Settings


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
