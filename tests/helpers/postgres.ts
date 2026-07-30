import { tmpdir } from "node:os";
import { join } from "node:path";
import EmbeddedPostgres from "embedded-postgres";
import { Pool } from "pg";

const embeddedDatabaseName = "vibepod_board_test";
const embeddedPort = 55432;
let embeddedStart: Promise<void> | undefined;

export let testDatabaseUrl =
  process.env.TEST_DATABASE_URL ??
  `postgres://vibepod:vibepod@localhost:${embeddedPort}/${embeddedDatabaseName}`;

export const ensureTestDatabase = async () => {
  if (process.env.TEST_DATABASE_URL) {
    testDatabaseUrl = process.env.TEST_DATABASE_URL;
    return;
  }

  embeddedStart ??= startEmbeddedPostgres();
  await embeddedStart;
};

export const createTestPool = () =>
  new Pool({
    connectionString: testDatabaseUrl,
    max: 1,
  });

export const resetDatabase = async (pool: Pool) => {
  await pool.query("DROP SCHEMA IF EXISTS public CASCADE");
  await pool.query("CREATE SCHEMA public");
};

const startEmbeddedPostgres = async () => {
  const postgres = new EmbeddedPostgres({
    databaseDir: join(tmpdir(), `vibepod-board-postgres-tests-${process.pid}`),
    user: "vibepod",
    password: "vibepod",
    port: embeddedPort,
    persistent: false,
    onLog: () => undefined,
    onError: () => undefined,
  });

  await postgres.initialise();
  await postgres.start();
  try {
    await postgres.createDatabase(embeddedDatabaseName);
  } catch (error) {
    if (
      !(error instanceof Error) ||
      !error.message.includes("already exists")
    ) {
      throw error;
    }
  }
};
