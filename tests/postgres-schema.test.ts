import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createPool, initializeDatabase } from "../src/server/db.js";
import {
  createTestPool,
  ensureTestDatabase,
  resetDatabase,
  testDatabaseUrl,
} from "./helpers/postgres.js";

let pool: Pool;

beforeEach(async () => {
  await ensureTestDatabase();
  pool = createTestPool();
  await resetDatabase(pool);
});

afterEach(async () => {
  await pool.end();
});

describe("PostgreSQL schema", () => {
  it("creates board and token tables", async () => {
    await initializeDatabase(pool);

    const tables = await pool.query<{ table_name: string }>(
      "select table_name from information_schema.tables where table_schema = 'public' order by table_name",
    );

    expect(tables.rows.map((row) => row.table_name)).toEqual([
      "activity_events",
      "api_token_projects",
      "api_tokens",
      "board_cards",
      "documents",
      "idea_readiness_events",
      "ideas",
      "projects",
      "task_dependencies",
    ]);
  });

  it("creates a pool from a database URL", async () => {
    const created = createPool(testDatabaseUrl);
    try {
      expect(created.options.connectionString).toBe(testDatabaseUrl);
    } finally {
      await created.end();
    }
  });

  it("stores branch names on board cards", async () => {
    await initializeDatabase(pool);

    const columns = await pool.query<{ column_name: string }>(
      `select column_name
       from information_schema.columns
       where table_schema = 'public' and table_name = 'board_cards'
       order by ordinal_position`,
    );

    expect(columns.rows.map((row) => row.column_name)).toContain("branch_name");
  });

  it("stores repository metadata on ideas and board cards", async () => {
    await initializeDatabase(pool);

    const columns = await pool.query<{
      table_name: string;
      column_name: string;
    }>(
      `select table_name, column_name
       from information_schema.columns
       where table_schema = 'public'
         and table_name in ('ideas', 'board_cards')
       order by table_name, ordinal_position`,
    );
    const columnsByTable = new Map<string, string[]>();
    for (const row of columns.rows) {
      columnsByTable.set(row.table_name, [
        ...(columnsByTable.get(row.table_name) ?? []),
        row.column_name,
      ]);
    }

    expect(columnsByTable.get("ideas")).toEqual(
      expect.arrayContaining([
        "repository_local_path",
        "repository_remote_url",
      ]),
    );
    expect(columnsByTable.get("board_cards")).toEqual(
      expect.arrayContaining([
        "repository_local_path",
        "repository_remote_url",
      ]),
    );
  });

  it("stores an assignee on ideas and board cards", async () => {
    await initializeDatabase(pool);

    const columns = await pool.query<{ table_name: string }>(
      `select table_name
       from information_schema.columns
       where table_schema = 'public'
         and table_name in ('ideas', 'board_cards')
         and column_name = 'assignee'
       order by table_name`,
    );

    expect(columns.rows.map((row) => row.table_name)).toEqual([
      "board_cards",
      "ideas",
    ]);
  });

  it("stores readiness fields on board cards", async () => {
    await initializeDatabase(pool);

    const columns = await pool.query<{ column_name: string }>(
      `select column_name
       from information_schema.columns
       where table_schema = 'public' and table_name = 'board_cards'
       order by ordinal_position`,
    );

    const names = columns.rows.map((row) => row.column_name);
    expect(names).toContain("readiness_score");
    expect(names).toContain("readiness_reason");
    expect(names).toContain("readiness_evaluated_at");
  });

  it("stores idea readiness events", async () => {
    await initializeDatabase(pool);

    const columns = await pool.query<{ column_name: string }>(
      `select column_name
       from information_schema.columns
       where table_schema = 'public' and table_name = 'idea_readiness_events'
       order by ordinal_position`,
    );

    const names = columns.rows.map((row) => row.column_name);
    expect(names).toEqual(
      expect.arrayContaining([
        "id",
        "idea_id",
        "score",
        "reason",
        "created_at",
      ]),
    );
  });

  it("indexes the lookups the board actually runs", async () => {
    await initializeDatabase(pool);

    const indexes = await pool.query<{ indexname: string }>(
      "select indexname from pg_indexes where schemaname = 'public'",
    );

    const names = indexes.rows.map((row) => row.indexname);
    // board_cards.idea_id is the predicate behind card lookup, the readiness
    // fan-out and the dependency join, and carried no index before.
    expect(names).toContain("board_cards_idea_idx");
    expect(names).toContain("ideas_project_status_updated_idx");
    expect(names).toContain("board_cards_project_column_updated_idx");
    expect(names).toContain("ideas_project_updated_id_idx");
    expect(names).toContain("board_cards_project_updated_id_idx");
  });

  it("stores readiness fields on ideas", async () => {
    await initializeDatabase(pool);

    const columns = await pool.query<{ column_name: string }>(
      `select column_name
       from information_schema.columns
       where table_schema = 'public' and table_name = 'ideas'
       order by ordinal_position`,
    );

    const names = columns.rows.map((row) => row.column_name);
    expect(names).toContain("readiness_score");
    expect(names).toContain("readiness_reason");
    expect(names).toContain("readiness_evaluated_at");
  });
});
