import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const compose = readFileSync(join(process.cwd(), "compose.yml"), "utf8");

describe("compose networking and database", () => {
  it("attaches the board service to the shared VibePod network", () => {
    expect(compose).toContain("vibepod:");
    expect(compose).toContain("aliases:");
    expect(compose).toContain("- vibepod-board");
    expect(compose).toContain("name: ${VIBEPOD_NETWORK:-vibepod-network}");
    expect(compose).toContain("external: true");
  });

  it("runs PostgreSQL as the board storage service", () => {
    expect(compose).toContain("postgres:");
    expect(compose).toContain("image: postgres:16-alpine");
    expect(compose).toContain("DATABASE_URL:");
    expect(compose).toContain("vibepod-board-postgres-data:");
    expect(compose).not.toContain("DATA_DIR: /data");
    expect(compose).not.toContain("vibepod-board-data:/data");
  });
});
