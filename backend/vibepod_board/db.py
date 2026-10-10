import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from alembic import command
from alembic.config import Config
from fastapi import Depends
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlmodel import Session

logger = logging.getLogger(__name__)

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


# An early build numbered its suggested pull request migration 0012 (after 0011); it became
# 0014 once 0012 and 0013 landed before it. A database that ran that build is at "0012" with
# these columns but without the real 0012 (task_runs.kind and the other run report fields).
EARLY_SUGGESTED_PR_REVISION = "0012"
EARLY_SUGGESTED_PR_COLUMNS = (
    "suggested_pr_title",
    "suggested_pr_body",
    "suggested_pr_by",
    "suggested_pr_at",
)
LEGACY_PREFIX = "legacy_"


def _columns(connection: Connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table)}


def _set_aside_early_suggested_prs(connection: Connection, config: Config) -> bool:
    """Detects a database migrated by the early 0012 and readies it for the real chain: its
    suggested pull request columns are renamed aside and it is stamped back to 0011, so 0012
    through 0014 apply as written. Says whether it did; any other database is left alone."""
    tables = set(inspect(connection).get_table_names())
    if not {"alembic_version", "board_cards", "task_runs"} <= tables:
        return False
    versions = connection.execute(text("select version_num from alembic_version")).scalars()
    if list(versions) != [EARLY_SUGGESTED_PR_REVISION]:
        return False
    if not set(EARLY_SUGGESTED_PR_COLUMNS) <= _columns(connection, "board_cards"):
        return False
    if "kind" in _columns(connection, "task_runs"):
        return False
    logger.warning(
        "Repairing a database migrated by an early build: its 0012 added the suggested pull "
        "request columns that are now 0014; setting them aside and replaying 0012 to 0014"
    )
    for name in EARLY_SUGGESTED_PR_COLUMNS:
        connection.execute(
            text(f"alter table board_cards rename column {name} to {LEGACY_PREFIX}{name}")
        )
    command.stamp(config, "0011")
    return True


def _restore_early_suggested_prs(connection: Connection) -> None:
    """Moves the values set aside by `_set_aside_early_suggested_prs` into the columns 0014
    added, then drops the set-aside columns."""
    assignments = ", ".join(
        f"{name} = {LEGACY_PREFIX}{name}" for name in EARLY_SUGGESTED_PR_COLUMNS
    )
    connection.execute(text(f"update board_cards set {assignments}"))
    for name in EARLY_SUGGESTED_PR_COLUMNS:
        connection.execute(text(f"alter table board_cards drop column {LEGACY_PREFIX}{name}"))


def create_db_engine(url: str, pool_size: int = 10) -> Engine:
    return create_engine(url, pool_size=pool_size, pool_pre_ping=True)


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


def migrate(engine: Engine) -> None:
    """Brings any database to the latest revision.

    A database the TypeScript server created has the tables but no `alembic_version`;
    it is caught up to the baseline and stamped, so no data is recreated or lost. One an
    early build migrated with a differently numbered 0012 is repaired the same way: all in
    one transaction, so a failed repair leaves the database as it was.
    """
    config = alembic_config()
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        config.attributes["connection"] = connection
        if "projects" in tables and "alembic_version" not in tables:
            connection.execute(text(LEGACY_CATCH_UP_SQL))
            command.stamp(config, BASELINE_REVISION)
        repaired = _set_aside_early_suggested_prs(connection, config)
        command.upgrade(config, "head")
        if repaired:
            _restore_early_suggested_prs(connection)


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
