import type { Pool } from "pg";

import { initializeDatabase } from "../../src/server/db.js";
import { PostgresBoardStore } from "../../src/server/storage.js";
import { createTestPool, ensureTestDatabase, resetDatabase } from "./postgres.js";

export const createTestStore = async () => {
  await ensureTestDatabase();
  const pool = createTestPool();
  await resetDatabase(pool);
  await initializeDatabase(pool);
  const store = new PostgresBoardStore(pool);
  return { pool, store };
};

export const closeTestPool = async (pool: Pool) => {
  await pool.end();
};
