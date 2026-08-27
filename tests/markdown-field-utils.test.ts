import { describe, expect, it } from "vitest";

import {
  criteriaPreviewItems,
  hasMarkdownContent,
  markdownFieldModes,
  normalizeMarkdownFieldMode,
  toggleMarkdownFieldMode,
} from "../src/client/markdownField.js";

describe("markdown field utilities", () => {
  it("offers exactly an edit and a preview mode", () => {
    expect(markdownFieldModes).toEqual(["edit", "preview"]);
  });

  it("switches between editing and previewing", () => {
    expect(toggleMarkdownFieldMode("edit")).toBe("preview");
    expect(toggleMarkdownFieldMode("preview")).toBe("edit");
  });

  it("falls back to edit mode for unknown control values", () => {
    expect(normalizeMarkdownFieldMode("preview")).toBe("preview");
    expect(normalizeMarkdownFieldMode("edit")).toBe("edit");
    expect(normalizeMarkdownFieldMode(null)).toBe("edit");
    expect(normalizeMarkdownFieldMode("something-else")).toBe("edit");
  });

  it("treats whitespace-only content as empty", () => {
    expect(hasMarkdownContent("")).toBe(false);
    expect(hasMarkdownContent("   \n  ")).toBe(false);
    expect(hasMarkdownContent("# Goal")).toBe(true);
  });

  it("previews acceptance criteria as the list that will be saved", () => {
    expect(criteriaPreviewItems("Renders `code`\nLinks work")).toEqual([
      "Renders `code`",
      "Links work",
    ]);
    expect(criteriaPreviewItems("  \n\n")).toEqual([]);
    expect(criteriaPreviewItems("Same\nSame")).toEqual(["Same"]);
  });
});
