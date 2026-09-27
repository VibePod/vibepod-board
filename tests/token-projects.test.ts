import { describe, expect, it } from "vitest";

import { formatTokenProjectLabel } from "../src/client/tokenProjects.js";

describe("formatTokenProjectLabel", () => {
  it("shows the project key and title", () => {
    expect(
      formatTokenProjectLabel({ id: "p1", key: "VP", title: "VibePod" }),
    ).toBe("VP · VibePod");
  });

  it("falls back to the key when the title is blank", () => {
    expect(formatTokenProjectLabel({ id: "p1", key: "VP", title: "  " })).toBe(
      "VP",
    );
  });
});
