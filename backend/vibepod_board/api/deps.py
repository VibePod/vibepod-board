from typing import Annotated

from fastapi import Depends, Request

from vibepod_board.config import Settings
from vibepod_board.services.workers import WorkerTiming


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def worker_timing(settings: Settings) -> WorkerTiming:
    return WorkerTiming(
        heartbeat_seconds=settings.worker_heartbeat_seconds,
        offline_seconds=settings.worker_offline_seconds,
        lease_seconds=settings.claim_lease_seconds,
    )
