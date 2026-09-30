import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_PUBLIC_DIR = Path(__file__).resolve().parents[2] / "dist" / "client"


def to_sqlalchemy_url(database_url: str) -> str:
    """Accepts the `postgres://` URLs the TS server used and selects the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    return database_url


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_username: str
    admin_password: str
    public_dir: Path | None
    auto_migrate: bool
    github_token: str | None
    github_repository: str | None
    pool_size: int
    # Automated claims: the default lease, the failed attempts before a task is blocked, and
    # how often lapsed claims are swept (0 turns the sweep off).
    claim_lease_seconds: int = 15 * 60
    claim_max_attempts: int = 3
    claim_sweep_seconds: int = 15
    # Workers: how often they should send a heartbeat, and after how long without one they
    # are shown as offline.
    worker_heartbeat_seconds: int = 15
    worker_offline_seconds: int = 60

    @property
    def sqlalchemy_url(self) -> str:
        return to_sqlalchemy_url(self.database_url)


@lru_cache
def get_settings() -> Settings:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")
    if os.environ.get("APP_ENV") == "production" and not (username and password):
        raise RuntimeError("ADMIN_USERNAME and ADMIN_PASSWORD are required in production")
    public_dir = Path(os.environ.get("PUBLIC_DIR", DEFAULT_PUBLIC_DIR))
    heartbeat_seconds = _positive_int("WORKER_HEARTBEAT_SECONDS", 15)
    offline_seconds = _positive_int("WORKER_OFFLINE_SECONDS", 60)
    # A worker waits a heartbeat interval between reports, and scheduling adds jitter: an
    # offline timeout under two intervals shows healthy workers as offline.
    if offline_seconds < 2 * heartbeat_seconds:
        raise RuntimeError(
            f"WORKER_OFFLINE_SECONDS must be at least twice WORKER_HEARTBEAT_SECONDS: "
            f"{offline_seconds} < 2 × {heartbeat_seconds}"
        )
    lease_seconds = _lease_seconds("CLAIM_LEASE_SECONDS", 15 * 60)
    # Heartbeats renew claims for the default lease, so it has to outlast the wait between two.
    if lease_seconds < 2 * heartbeat_seconds:
        raise RuntimeError(
            f"CLAIM_LEASE_SECONDS must be at least twice WORKER_HEARTBEAT_SECONDS: "
            f"{lease_seconds} < 2 × {heartbeat_seconds}"
        )
    return Settings(
        database_url=database_url,
        admin_username=username or "admin",
        admin_password=password or "admin",
        public_dir=public_dir if (public_dir / "index.html").exists() else None,
        auto_migrate=os.environ.get("ALEMBIC_AUTO_UPGRADE", "true").lower() != "false",
        github_token=os.environ.get("GITHUB_TOKEN") or None,
        github_repository=os.environ.get("GITHUB_REPOSITORY") or None,
        pool_size=int(os.environ.get("DATABASE_POOL_MAX", "10")),
        claim_lease_seconds=lease_seconds,
        claim_max_attempts=_positive_int("CLAIM_MAX_ATTEMPTS", 3),
        claim_sweep_seconds=_positive_int("CLAIM_SWEEP_SECONDS", 15, allow_zero=True),
        worker_heartbeat_seconds=heartbeat_seconds,
        worker_offline_seconds=offline_seconds,
    )


def _lease_seconds(name: str, default: int) -> int:
    """A lease inside the range claims accept, so a bad value fails at startup rather than
    on every claim."""
    from vibepod_board.services.claims import MAX_LEASE_SECONDS, MIN_LEASE_SECONDS

    value = _positive_int(name, default)
    if not MIN_LEASE_SECONDS <= value <= MAX_LEASE_SECONDS:
        raise RuntimeError(
            f"{name} must be between {MIN_LEASE_SECONDS} and {MAX_LEASE_SECONDS}: {value}"
        )
    return value


def _positive_int(name: str, default: int, allow_zero: bool = False) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer: {raw}") from error
    if value < 0 or (value == 0 and not allow_zero):
        raise RuntimeError(f"{name} must be {'zero or ' if allow_zero else ''}positive: {raw}")
    return value
