import { describe, expect, it } from "vitest";

import { integrationExamplesForToken } from "../src/shared/integrationExamples.js";

describe("token-aware MCP config examples", () => {
  it("includes the bearer token in every config example", () => {
    const examples = integrationExamplesForToken("vbp_test_token");

    for (const example of examples) {
      expect(`${example.command ?? ""}\n${example.config}`).toContain(
        "vbp_test_token",
      );
      expect(`${example.command ?? ""}\n${example.config}`).toContain(
        "Authorization",
      );
    }
  });
});
