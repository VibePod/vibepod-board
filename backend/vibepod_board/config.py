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
    return Settings(
        database_url=database_url,
        admin_username=username or "admin",
        admin_password=password or "admin",
        public_dir=public_dir if (public_dir / "index.html").exists() else None,
        auto_migrate=os.environ.get("ALEMBIC_AUTO_UPGRADE", "true").lower() != "false",
        github_token=os.environ.get("GITHUB_TOKEN") or None,
        github_repository=os.environ.get("GITHUB_REPOSITORY") or None,
        pool_size=int(os.environ.get("DATABASE_POOL_MAX", "10")),
    )
