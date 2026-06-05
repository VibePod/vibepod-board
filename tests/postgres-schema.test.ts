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
});
