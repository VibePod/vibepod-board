from datetime import UTC, datetime
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlmodel import SQLModel

from vibepod_board.db import migrate

HEAD = "0005"
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
    assert {"branch_name", "archived_at"} <= set(column_names(db, "board_cards"))
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

    assert revision(empty_db) == HEAD
    assert schema_diff(empty_db) == []
    with empty_db.connect() as connection:
        assert connection.execute(text("select key from projects")).scalars().all() == ["APP"]


# What the TypeScript server on the agent-API branch added on every start.
AGENT_API_SCHEMA = """
create index if not exists board_cards_idea_idx on board_cards(idea_id);
create index if not exists ideas_project_status_updated_idx
  on ideas(project_id, status, updated_at desc);
create index if not exists board_cards_project_column_updated_idx
  on board_cards(project_id, column_name, updated_at desc);
create index if not exists ideas_project_updated_id_idx
  on ideas(project_id, updated_at desc, id desc);
create index if not exists board_cards_project_updated_id_idx
  on board_cards(project_id, updated_at desc, id desc);
alter table ideas add column if not exists assignee text;
alter table board_cards add column if not exists assignee text;
create index if not exists ideas_project_assignee_idx on ideas(project_id, assignee);
create index if not exists board_cards_project_assignee_idx on board_cards(project_id, assignee);
"""


def test_adopts_a_typescript_database_that_already_has_assignees(empty_db: Engine) -> None:
    with empty_db.begin() as connection:
        connection.exec_driver_sql(LEGACY_SCHEMA)
        connection.exec_driver_sql(AGENT_API_SCHEMA)
        connection.execute(
            text(
                "insert into projects (id, key, title, created_at, updated_at) "
                "values ('p1', 'APP', 'App', now(), now())"
            )
        )
        connection.execute(
            text(
                "insert into ideas (id, project_id, task_number, title, status, assignee, "
                "created_at, updated_at) "
                "values ('i1', 'p1', 1, 'Held', 'idea', 'Claude::Worker', now(), now())"
            )
        )

    migrate(empty_db)

    assert revision(empty_db) == HEAD
    assert schema_diff(empty_db) == []
    with empty_db.connect() as connection:
        assert connection.execute(text("select assignee from ideas")).scalars().all() == [
            "Claude::Worker"
        ]


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
    assert revision(db) == HEAD
    assert schema_diff(db) == []


def test_0003_adopts_links_from_the_old_sync_endpoint_and_retires_task_numbers(
    empty_db: Engine,
) -> None:
    from alembic import command

    from vibepod_board.db import alembic_config

    config = alembic_config()
    with empty_db.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0002")
        connection.execute(
            text(
                "insert into projects (id, key, title, created_at, updated_at) "
                "values ('p1', 'APP', 'App', now(), now())"
            )
        )
        for idea_id, number, url in [
            ("a", 1, "https://github.com/VibePod/Board/issues/7"),
            ("b", 2, "https://github.com/vibepod/board/issues/7"),  # same issue, other case
            ("c", 5, "https://example.com/not-github/1"),
        ]:
            connection.execute(
                text(
                    "insert into ideas (id, project_id, task_number, title, status, "
                    "github_issue_url, github_issue_number, created_at, updated_at) "
                    "values (:id, 'p1', :n, 'T', 'idea', :url, 7, now() + :n * interval '1 s', "
                    "now())"
                ),
                {"id": idea_id, "n": number, "url": url},
            )

    migrate(empty_db)

    with empty_db.connect() as connection:
        repositories = dict(
            connection.execute(text("select id, github_repository from ideas")).all()
        )
        last = connection.execute(text("select last_task_number from projects")).scalar_one()
    assert repositories == {"a": "vibepod/board", "b": None, "c": None}
    assert last == 5


def test_0004_adds_an_empty_archive_state_and_keeps_existing_cards(empty_db: Engine) -> None:
    from alembic import command

    from vibepod_board.db import alembic_config

    config = alembic_config()
    with empty_db.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0003")
        connection.execute(
            text(
                "insert into projects (id, key, title, created_at, updated_at) "
                "values ('p1', 'APP', 'App', now(), now())"
            )
        )
        connection.execute(
            text(
                "insert into board_cards (id, project_id, title, column_name, created_at, "
                "updated_at) values ('c1', 'p1', 'Shipped', 'done', now(), now())"
            )
        )

    migrate(empty_db)

    with empty_db.connect() as connection:
        rows = connection.execute(
            text("select id, column_name, archived_at from board_cards")
        ).all()
    assert [tuple(row) for row in rows] == [("c1", "done", None)]
    assert revision(empty_db) == HEAD
