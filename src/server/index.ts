import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createAdminSessionManager } from "./auth.js";
import { createApp } from "./app.js";
import { createPool, initializeDatabase } from "./db.js";
import { PostgresBoardStore } from "./storage.js";

const currentDir = dirname(fileURLToPath(import.meta.url));
const publicDir = resolve(currentDir, "../client");
const port = Number(process.env.PORT ?? 3000);
const host = process.env.HOST ?? "0.0.0.0";

const databaseUrl = process.env.DATABASE_URL;
const adminUsername = process.env.ADMIN_USERNAME;
const adminPassword = process.env.ADMIN_PASSWORD;

if (!databaseUrl) {
  throw new Error("DATABASE_URL is required");
}
if (process.env.NODE_ENV === "production" && (!adminUsername || !adminPassword)) {
  throw new Error("ADMIN_USERNAME and ADMIN_PASSWORD are required in production");
}

const pool = createPool(databaseUrl);
await initializeDatabase(pool);

const store = new PostgresBoardStore(pool);
const sessions = createAdminSessionManager({
  username: adminUsername ?? "admin",
  password: adminPassword ?? "admin"
});

const app = createApp({
  store,
  sessions,
  publicDir: existsSync(resolve(publicDir, "index.html")) ? publicDir : undefined
});

app.listen(port, host, () => {
  console.log(`vibepod-board listening on http://${host}:${port}`);
  console.log("Storage: PostgreSQL");
});
