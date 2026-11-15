import { describe, expect, it } from "vitest";

import {
  endpointOptions,
  integrationExamples,
  integrationExamplesForToken,
  oauthExamples,
} from "../src/shared/integrationExamples.js";

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

  it("adds Pi as a project-local server", () => {
    const pi = integrationExamples.find((example) => example.id === "pi");

    expect(pi?.name).toBe("Pi");
    expect(pi?.command).toBe(
      'pi mcp add vibepod-board --local --url http://vibepod-board:3000/mcp --header "Authorization=Bearer <vibepod-board-token>"',
    );
    expect(pi?.configLabel).toBe("Project .pi/mcp.json alternative");
    expect(JSON.parse(pi?.config ?? "null")).toEqual({
      mcpServers: {
        "vibepod-board": {
          url: "http://vibepod-board:3000/mcp",
          headers: { Authorization: "Bearer <vibepod-board-token>" },
        },
      },
    });
  });

  it("tells Pi users to trust the project", () => {
    const pi = integrationExamples.find((example) => example.id === "pi");

    expect(pi?.verify).toContain("trust it");
  });

  it("documents OAuth sign-in for Pi without a token", () => {
    const [pi] = oauthExamples;

    expect(pi.id).toBe("pi-oauth");
    expect(pi.command).toBe(
      "pi mcp add vibepod-board --local --url http://localhost:3000/mcp --oauth-callback-port 8765\npi mcp login vibepod-board",
    );
    expect(JSON.parse(pi.config)).toEqual({
      mcpServers: {
        "vibepod-board": {
          url: "http://localhost:3000/mcp",
          oauth: { callbackPort: 8765 },
        },
      },
    });
    expect(`${pi.command}\n${pi.config}`).not.toContain("Authorization");
    expect(pi.verify).toContain("trust it");
  });

  it("keeps OAuth sign-in out of the token snippets", () => {
    const ids = integrationExamplesForToken("vbp_test_token").map(
      (example) => example.id,
    );

    expect(ids).not.toContain("pi-oauth");
  });

  it("names Pi among the agents on the VibePod network", () => {
    expect(endpointOptions[0].description).toContain("Pi");
  });
});
