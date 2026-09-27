from datetime import UTC, datetime
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlmodel import SQLModel

from vibepod_board.db import migrate

LEGACY_SCHEMA = (Path(__file__).parent / "legacy_schema.sql").read_text()
# The first TS schema, before repository, readiness and dependency support.
ORIGINAL_SCHEMA = LEGACY_SCHEMA.split("alter table board_cards add column", 1)[0]


def table_names(engine: Engine) -> list[str]:
    return sorted(inspect(engine).get_table_names())


def column_names(engine: Engine, table: str) -> list[str]:
    return [column["name"] for column in inspect(engine).get_columns(table)]


def schema_diff(engine: Engine) -> list:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        return compare_metadata(context, SQLModel.metadata)


def revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(text("select version_num from alembic_version")).scalar_one()


def test_creates_board_and_token_tables(db: Engine) -> None:
    assert [name for name in table_names(db) if name != "alembic_version"] == [
        "activity_events",
        "api_token_projects",
        "api_tokens",
        "board_cards",
        "documents",
        "idea_readiness_events",
        "ideas",
        "projects",
        "task_dependencies",
    ]


def test_stores_branch_repository_and_readiness_columns(db: Engine) -> None:
    for table in ("ideas", "board_cards"):
        assert {
            "repository_local_path",
            "repository_remote_url",
            "readiness_score",
            "readiness_reason",
            "readiness_evaluated_at",
        } <= set(column_names(db, table))
    assert "branch_name" in column_names(db, "board_cards")
    assert {"id", "idea_id", "score", "reason", "created_at"} <= set(
        column_names(db, "idea_readiness_events")
    )


def test_models_match_the_migrated_schema(db: Engine) -> None:
    assert schema_diff(db) == []


def test_adopts_a_database_created_by_the_typescript_server(empty_db: Engine) -> None:
    with empty_db.begin() as connection:
        connection.exec_driver_sql(LEGACY_SCHEMA)
        connection.execute(
            text(
                "insert into projects (id, key, title, created_at, updated_at) "
                "values ('p1', 'APP', 'App', now(), now())"
            )
        )

    migrate(empty_db)

    assert revision(empty_db) == "0001"
    assert schema_diff(empty_db) == []
    with empty_db.connect() as connection:
        assert connection.execute(text("select key from projects")).scalars().all() == ["APP"]


def test_catches_up_an_original_typescript_database(empty_db: Engine) -> None:
    now = datetime.now(UTC)
    with empty_db.begin() as connection:
        connection.exec_driver_sql(ORIGINAL_SCHEMA)
        connection.execute(
            text(
                "insert into projects (id, key, title, created_at, updated_at) "
                "values ('p1', 'APP', 'App', :now, :now)"
            ),
            {"now": now},
        )

    migrate(empty_db)

    assert schema_diff(empty_db) == []
    assert "task_dependencies" in table_names(empty_db)


def test_migrating_twice_is_a_no_op(db: Engine) -> None:
    migrate(db)
    assert revision(db) == "0001"
    assert schema_diff(db) == []
