import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createApp } from "./app.js";
import { BoardStore } from "./storage.js";

const currentDir = dirname(fileURLToPath(import.meta.url));
const dataDir = process.env.DATA_DIR ?? resolve(process.cwd(), "data");
const dataFile = process.env.DATA_FILE ?? join(dataDir, "board.json");
const publicDir = resolve(currentDir, "../client");
const port = Number(process.env.PORT ?? 3000);
const host = process.env.HOST ?? "0.0.0.0";

const store = new BoardStore(dataFile);
const app = createApp({
  store,
  publicDir: existsSync(join(publicDir, "index.html")) ? publicDir : undefined
});

app.listen(port, host, () => {
  console.log(`vibepod-board listening on http://${host}:${port}`);
  console.log(`Data file: ${dataFile}`);
});
