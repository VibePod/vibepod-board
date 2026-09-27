from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from alembic import command
from alembic.config import Config
from fastapi import Depends
from sqlalchemy import Engine, create_engine, inspect, text
from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "0001"

# The TypeScript server evolved its schema with idempotent `add column if not exists`
# statements on every start. An older database may predate some of them, so a legacy
# database is brought up to the baseline with the same statements before it is stamped.
LEGACY_CATCH_UP_SQL = """
alter table board_cards add column if not exists branch_name text;
alter table ideas add column if not exists repository_local_path text;
alter table ideas add column if not exists repository_remote_url text;
alter table board_cards add column if not exists repository_local_path text;
alter table board_cards add column if not exists repository_remote_url text;
alter table board_cards add column if not exists readiness_score integer;
alter table board_cards add column if not exists readiness_reason text;
alter table board_cards add column if not exists readiness_evaluated_at timestamptz;
alter table ideas add column if not exists readiness_score integer;
alter table ideas add column if not exists readiness_reason text;
alter table ideas add column if not exists readiness_evaluated_at timestamptz;

create table if not exists task_dependencies (
  idea_id text not null references ideas(id) on delete cascade,
  depends_on_idea_id text not null references ideas(id) on delete cascade,
  created_at timestamptz not null,
  primary key (idea_id, depends_on_idea_id),
  check (idea_id <> depends_on_idea_id)
);
create index if not exists task_dependencies_depends_on_idx
  on task_dependencies(depends_on_idea_id);

create table if not exists idea_readiness_events (
  id text primary key,
  idea_id text not null references ideas(id) on delete cascade,
  score integer not null,
  reason text not null,
  created_at timestamptz not null
);
create index if not exists idea_readiness_events_idea_idx
  on idea_readiness_events(idea_id, created_at desc);

insert into idea_readiness_events (id, idea_id, score, reason, created_at)
select md5(random()::text || id), id, readiness_score,
       coalesce(readiness_reason, ''), coalesce(readiness_evaluated_at, now())
from ideas
where readiness_score is not null
  and not exists (select 1 from idea_readiness_events e where e.idea_id = ideas.id);
"""


def create_db_engine(url: str, pool_size: int = 10) -> Engine:
    return create_engine(url, pool_size=pool_size, pool_pre_ping=True)


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


def migrate(engine: Engine) -> None:
    """Brings any database to the latest revision.

    A database the TypeScript server created has the tables but no `alembic_version`;
    it is caught up to the baseline and stamped, so no data is recreated or lost.
    """
    config = alembic_config()
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        config.attributes["connection"] = connection
        if "projects" in tables and "alembic_version" not in tables:
            connection.execute(text(LEGACY_CATCH_UP_SQL))
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")


_engine: Engine | None = None


def set_engine(engine: Engine) -> None:
    global _engine
    _engine = engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database engine is not configured")
    return _engine


def get_session() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
