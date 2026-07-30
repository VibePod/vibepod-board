import { describe, expect, it } from "vitest";

import { integrationExamples } from "../src/shared/integrationExamples.js";

describe("integration examples", () => {
  it("covers the initial VibePod agent clients", () => {
    const ids = integrationExamples.map((example) => example.id);

    expect(ids).toEqual(
      expect.arrayContaining(["claude-code", "codex", "auggie", "opencode"]),
    );
  });

  it("uses the VibePod network endpoint in container examples", () => {
    for (const example of integrationExamples) {
      expect(`${example.command ?? ""}\n${example.config}`).toContain(
        "vibepod-board:3000/mcp",
      );
    }
  });
});
