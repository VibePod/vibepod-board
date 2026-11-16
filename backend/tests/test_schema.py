import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlmodel import SQLModel

from vibepod_board.db import migrate

HEAD = "0020"
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
        "oauth_clients",
        "oauth_requests",
        "projects",
        "task_dependencies",
        "task_events",
        "task_reviews",
        "task_runs",
        "workers",
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


def test_0006_leaves_existing_cards_unclaimed_and_unblocked(empty_db: Engine) -> None:
    from alembic import command

    from vibepod_board.db import alembic_config

    config = alembic_config()
    with empty_db.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0005")
        connection.execute(
            text(
                "insert into projects (id, key, title, created_at, updated_at) "
                "values ('p1', 'APP', 'App', now(), now())"
            )
        )
        connection.execute(
            text(
                "insert into board_cards (id, project_id, title, column_name, created_at, "
                "updated_at) values ('c1', 'p1', 'Planned work', 'planned', now(), now())"
            )
        )

    migrate(empty_db)

    with empty_db.connect() as connection:
        rows = connection.execute(
            text("select claimed_at, claim_expires_at, attempts, blocked_at from board_cards")
        ).all()
    assert [tuple(row) for row in rows] == [(None, None, 0, None)]
    assert "task_events" in table_names(empty_db)
    assert revision(empty_db) == HEAD


def test_0012_leaves_existing_cards_without_a_suggested_pull_request(empty_db: Engine) -> None:
    from alembic import command

    from vibepod_board.db import alembic_config

    config = alembic_config()
    with empty_db.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0011")
        connection.execute(
            text(
                "insert into projects (id, key, title, created_at, updated_at) "
                "values ('p1', 'APP', 'App', now(), now())"
            )
        )
        connection.execute(
            text(
                "insert into board_cards (id, project_id, title, column_name, created_at, "
                "updated_at) values ('c1', 'p1', 'Reviewed work', 'review', now(), now())"
            )
        )

    migrate(empty_db)

    with empty_db.connect() as connection:
        rows = connection.execute(
            text(
                "select suggested_pr_title, suggested_pr_body, suggested_pr_by, "
                "suggested_pr_at from board_cards"
            )
        ).all()
    assert [tuple(row) for row in rows] == [(None, None, None, None)]
    assert revision(empty_db) == HEAD


@pytest.mark.parametrize("pruned", [False, True])
def test_0013_keeps_existing_review_run_verdicts(empty_db: Engine, pruned: bool) -> None:
    from alembic import command

    from vibepod_board.db import alembic_config

    config = alembic_config()
    with empty_db.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0012")
        connection.execute(
            text("""
            INSERT INTO projects (id, key, title, created_at, updated_at)
            VALUES ('p', 'VP', 'VibePod', now(), now());
            INSERT INTO ideas (id, project_id, task_number, title, status, created_at, updated_at)
            VALUES ('t', 'p', 1, 'Task', 'ready', now(), now());
            INSERT INTO workers (id, project_id, name, mode, started_at, last_seen_at)
            VALUES ('w', 'p', 'Reviewer', 'review', now(), now());
            INSERT INTO task_reviews (id, idea_id, project_id, reviewer, worker_id,
                verdict, feedback, lease_expires_at, ended_at, created_at, updated_at)
            VALUES ('review', 't', 'p', 'Reviewer', 'w', 'approve', 'Looks good', now(),
                now(), now(), now());
            INSERT INTO task_runs (id, idea_id, worker_id, worker_name, outcome, verify_output,
                started_at, created_at)
            VALUES ('run', 't', 'w', 'Reviewer', 'done', 'Tests passed', now(), now());
        """)
        )
    if pruned:
        with empty_db.begin() as connection:
            connection.execute(text("DELETE FROM workers WHERE id = 'w'"))
    migrate(empty_db)
    with empty_db.connect() as connection:
        result = connection.execute(
            text("SELECT kind, verdict, feedback, verify_output FROM task_runs")
        ).one()
    assert tuple(result) == ("review", "approve", "Looks good", "Tests passed")


def test_0016_keeps_existing_projects_active(empty_db: Engine) -> None:
    from alembic import command

    from vibepod_board.db import alembic_config

    config = alembic_config()
    with empty_db.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0015")
        connection.execute(
            text(
                "insert into projects (id, key, title, created_at, updated_at) "
                "values ('p1', 'APP', 'App', now(), now())"
            )
        )

    migrate(empty_db)

    with empty_db.connect() as connection:
        rows = connection.execute(text("select key, archived_at from projects")).all()
    assert [tuple(row) for row in rows] == [("APP", None)]
    assert revision(empty_db) == HEAD


def test_0018_gives_existing_tokens_and_sign_ins_full_access(empty_db: Engine) -> None:
    from alembic import command

    from vibepod_board.db import alembic_config
    from vibepod_board.permissions import FULL_ACCESS

    config = alembic_config()
    with empty_db.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0017")
        connection.execute(
            text(
                "insert into api_tokens (id, name, token_hash, created_at) "
                "values ('t1', 'Agent', 'hash', now())"
            )
        )
        connection.execute(text(CLIENT))
        connection.execute(text(REQUEST.format(extra_columns="", extra_values="")))
        command.upgrade(config, "0018")

    with empty_db.connect() as connection:
        token = connection.execute(text("select permissions from api_tokens")).scalar_one()
        request = connection.execute(text("select permissions from oauth_requests")).scalar_one()
    assert token == FULL_ACCESS
    assert request == FULL_ACCESS


CLIENT = "insert into oauth_clients (client_id, info, created_at) values ('c1', '{}'::jsonb, now())"
REQUEST = (
    "insert into oauth_requests (id, client_id, redirect_uri, "
    "redirect_uri_provided_explicitly, code_challenge, created_at, expires_at{extra_columns}) "
    "values ('r1', 'c1', 'http://127.0.0.1:1/callback', true, 'challenge', now(), now()"
    "{extra_values})"
)


def test_0019_moves_a_tokens_permissions_onto_each_of_its_projects(empty_db: Engine) -> None:
    from alembic import command

    from vibepod_board.db import alembic_config
    from vibepod_board.permissions import FULL_ACCESS

    reader = {**dict.fromkeys(FULL_ACCESS, "none"), "tasks": "read"}
    config = alembic_config()
    with empty_db.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0018")
        for key in ("APP", "API"):
            connection.execute(
                text(
                    "insert into projects (id, key, title, created_at, updated_at) "
                    f"values ('{key.lower()}', '{key}', '{key}', now(), now())"
                )
            )
        connection.execute(
            text(
                "insert into api_tokens (id, name, token_hash, permissions, created_at) values "
                "('reader', 'Reader', 'hash-1', cast(:reader as jsonb), now()), "
                "('full', 'Full', 'hash-2', cast(:full as jsonb), now())"
            ),
            {"reader": json.dumps(reader), "full": json.dumps(FULL_ACCESS)},
        )
        connection.execute(
            text(
                "insert into api_token_projects (token_id, project_id) values "
                "('reader', 'app'), ('reader', 'api'), ('full', 'app')"
            )
        )
        connection.execute(text(CLIENT))
        connection.execute(
            text(
                REQUEST.format(
                    extra_columns=", project_ids, permissions",
                    extra_values=', \'["app", "api"]\'::jsonb, cast(:reader as jsonb)',
                )
            ),
            {"reader": json.dumps(reader)},
        )

    migrate(empty_db)

    with empty_db.connect() as connection:
        mapped = connection.execute(
            text(
                "select token_id, project_id, permissions from api_token_projects "
                "order by token_id, project_id"
            )
        ).all()
        request = connection.execute(text("select permissions from oauth_requests")).scalar_one()
    assert [tuple(row) for row in mapped] == [
        ("full", "app", FULL_ACCESS),
        ("reader", "api", reader),
        ("reader", "app", reader),
    ]
    assert request == {"app": reader, "api": reader}
    assert "permissions" not in column_names(empty_db, "api_tokens")
    assert revision(empty_db) == HEAD


def test_0019_downgrade_never_widens_what_a_token_or_sign_in_may_do(empty_db: Engine) -> None:
    from alembic import command

    from vibepod_board.db import alembic_config
    from vibepod_board.permissions import FULL_ACCESS

    none = dict.fromkeys(FULL_ACCESS, "none")
    in_app = {**none, "tasks": "write", "board": "read", "documents": "write"}
    in_api = {**none, "tasks": "read", "board": "write", "documents": "write"}
    migrate(empty_db)
    with empty_db.begin() as connection:
        for key in ("APP", "API"):
            connection.execute(
                text(
                    "insert into projects (id, key, title, created_at, updated_at) "
                    f"values ('{key.lower()}', '{key}', '{key}', now(), now())"
                )
            )
        connection.execute(
            text(
                "insert into api_tokens (id, name, token_hash, created_at) values "
                "('mixed', 'Mixed', 'hash-1', now()), ('orphan', 'Orphan', 'hash-2', now())"
            )
        )
        connection.execute(
            text(
                "insert into api_token_projects (token_id, project_id, permissions) values "
                "('mixed', 'app', cast(:app as jsonb)), ('mixed', 'api', cast(:api as jsonb))"
            ),
            {"app": json.dumps(in_app), "api": json.dumps(in_api)},
        )
        connection.execute(text(CLIENT))
        connection.execute(
            text(
                REQUEST.format(
                    extra_columns=", project_ids, permissions",
                    extra_values=", '[\"app\"]'::jsonb, cast(:approved as jsonb)",
                )
            ),
            {"approved": json.dumps({"app": in_app})},
        )

    config = alembic_config()
    with empty_db.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0018")

    with empty_db.connect() as connection:
        tokens = dict(
            connection.execute(text("select id, permissions from api_tokens")).all()  # type: ignore[arg-type]
        )
        requests = connection.execute(text("select count(*) from oauth_requests")).scalar_one()
    # The one set a token gets is the lowest level any of its projects had in each area.
    assert tokens["mixed"] == {**none, "tasks": "read", "board": "read", "documents": "write"}
    # A token without projects reaches nothing, whatever its set says.
    assert tokens["orphan"] == FULL_ACCESS
    # A sign-in that was approved per project cannot be redeemed for a broader token.
    assert requests == 0


def test_0020_adds_the_review_identity_column_that_a_renumbered_migration_left_out(
    empty_db: Engine,
) -> None:
    """A database that an unmerged branch build migrated counts revision 0014 as applied,
    although that branch numbered another migration 0014: `task_reviews.actor_identity`
    was never added, and moving a card by hand failed on the missing column."""
    from alembic import command

    from vibepod_board.db import alembic_config

    config = alembic_config()
    with empty_db.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0019")
        # What such a database looks like once it reached 0019.
        connection.execute(text("alter table task_reviews drop column actor_identity"))
    assert "actor_identity" not in column_names(empty_db, "task_reviews")

    migrate(empty_db)

    assert "actor_identity" in column_names(empty_db, "task_reviews")
    assert schema_diff(empty_db) == []
    assert revision(empty_db) == HEAD
