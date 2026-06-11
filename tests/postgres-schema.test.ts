import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { Pool } from "pg";

import { createPool, initializeDatabase } from "../src/server/db.js";
import {
  createTestPool,
  ensureTestDatabase,
  resetDatabase,
  testDatabaseUrl
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
      "select table_name from information_schema.tables where table_schema = 'public' order by table_name"
    );

    expect(tables.rows.map((row) => row.table_name)).toEqual([
      "activity_events",
      "api_token_projects",
      "api_tokens",
      "board_cards",
      "documents",
      "ideas",
      "projects"
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
       order by ordinal_position`
    );

    expect(columns.rows.map((row) => row.column_name)).toContain("branch_name");
  });

  it("stores repository metadata on ideas and board cards", async () => {
    await initializeDatabase(pool);

    const columns = await pool.query<{ table_name: string; column_name: string }>(
      `select table_name, column_name
       from information_schema.columns
       where table_schema = 'public'
         and table_name in ('ideas', 'board_cards')
       order by table_name, ordinal_position`
    );
    const columnsByTable = new Map<string, string[]>();
    for (const row of columns.rows) {
      columnsByTable.set(row.table_name, [...(columnsByTable.get(row.table_name) ?? []), row.column_name]);
    }

    expect(columnsByTable.get("ideas")).toEqual(
      expect.arrayContaining(["repository_local_path", "repository_remote_url"])
    );
    expect(columnsByTable.get("board_cards")).toEqual(
      expect.arrayContaining(["repository_local_path", "repository_remote_url"])
    );
  });
});
