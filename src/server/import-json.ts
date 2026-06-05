import { createPool, initializeDatabase } from "./db.js";
import { importBoardJsonFile } from "./jsonImport.js";

const filePath = process.argv[2];

if (!filePath) {
  console.error("Usage: npm run import:json -- ./data/board.json");
  process.exit(1);
}

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) {
  console.error("DATABASE_URL is required");
  process.exit(1);
}

const pool = createPool(databaseUrl);

try {
  await initializeDatabase(pool);
  await importBoardJsonFile(pool, filePath);
  console.log(`Imported board JSON from ${filePath}`);
} finally {
  await pool.end();
}
