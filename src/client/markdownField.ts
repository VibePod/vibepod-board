import { parseListField } from "./formUtils.js";

export type MarkdownFieldMode = "edit" | "preview";

export const markdownFieldModes: MarkdownFieldMode[] = ["edit", "preview"];

export const toggleMarkdownFieldMode = (
  mode: MarkdownFieldMode,
): MarkdownFieldMode => (mode === "edit" ? "preview" : "edit");

export const normalizeMarkdownFieldMode = (
  value: string | null,
): MarkdownFieldMode => (value === "preview" ? "preview" : "edit");

export const hasMarkdownContent = (value: string): boolean =>
  value.trim().length > 0;

/**
 * Acceptance criteria are stored as a list, so the preview shows the criteria
 * exactly as they will be saved instead of the raw textarea content.
 */
export const criteriaPreviewItems = (value: string): string[] =>
  parseListField(value);
