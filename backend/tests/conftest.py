import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlmodel import Session

from vibepod_board.db import create_db_engine, migrate, set_engine


def _pg_bin(name: str) -> str:
    pg_bin = os.environ.get("PG_BIN")
    path = str(Path(pg_bin) / name) if pg_bin else shutil.which(name)
    if not path or not Path(path).exists():
        pytest.exit(f"{name} not found: set PG_BIN or TEST_DATABASE_URL", returncode=1)
    return path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A throwaway Postgres cluster, or the database named by TEST_DATABASE_URL."""
    if url := os.environ.get("TEST_DATABASE_URL"):
        yield url
        return

    data_dir = Path(tempfile.mkdtemp(prefix="vibepod-board-pg-"))
    port = _free_port()
    subprocess.run(
        [_pg_bin("initdb"), "-D", str(data_dir), "-U", "postgres", "-A", "trust", "-E", "UTF8"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            _pg_bin("pg_ctl"),
            "-D",
            str(data_dir),
            "-o",
            f"-p {port} -k {data_dir} -c listen_addresses=127.0.0.1 -c fsync=off",
            "-l",
            str(data_dir / "server.log"),
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    try:
        yield f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [_pg_bin("pg_ctl"), "-D", str(data_dir), "-m", "immediate", "stop"],
            capture_output=True,
        )
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_db_engine(database_url, pool_size=5)
    set_engine(engine)
    yield engine
    engine.dispose()


def reset_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("drop schema public cascade"))
        connection.execute(text("create schema public"))


@pytest.fixture
def empty_db(engine: Engine) -> Engine:
    """A database with no tables at all, for migration tests."""
    reset_schema(engine)
    return engine


@pytest.fixture
def db(engine: Engine) -> Engine:
    """A freshly migrated, empty database."""
    reset_schema(engine)
    migrate(engine)
    return engine


@pytest.fixture
def session(db: Engine) -> Iterator[Session]:
    with Session(db, expire_on_commit=False) as session:
        yield session
