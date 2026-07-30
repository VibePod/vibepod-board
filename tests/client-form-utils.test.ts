import { describe, expect, it } from "vitest";

import { formatListField, parseListField } from "../src/client/formUtils.js";

describe("client form list helpers", () => {
  it("parses comma and newline separated values into a trimmed unique list", () => {
    expect(parseListField("api, ui\napi\n  mcp  ")).toEqual([
      "api",
      "ui",
      "mcp",
    ]);
  });

  it("formats list values one per line for editable textareas", () => {
    expect(
      formatListField(["Ready ideas can be edited", "Notes can be edited"]),
    ).toBe("Ready ideas can be edited\nNotes can be edited");
  });
});
