import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const compose = readFileSync(join(process.cwd(), "compose.yml"), "utf8");

describe("compose networking", () => {
  it("attaches the board service to the shared VibePod network", () => {
    expect(compose).toContain("vibepod:");
    expect(compose).toContain("aliases:");
    expect(compose).toContain("- vibepod-board");
    expect(compose).toContain("name: ${VIBEPOD_NETWORK:-vibepod-network}");
    expect(compose).toContain("external: true");
  });
});
