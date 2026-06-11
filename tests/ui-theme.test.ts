import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const clientEntry = readFileSync(join(process.cwd(), "src/client/main.tsx"), "utf8");
const styles = readFileSync(join(process.cwd(), "src/client/styles.css"), "utf8");

describe("UI theme and card readability styles", () => {
  it("uses a Mantine theme instead of relying on default colors", () => {
    expect(clientEntry).toContain("createTheme");
    expect(clientEntry).toContain("theme={appTheme}");
    expect(clientEntry).toContain("primaryColor: \"teal\"");
  });

  it("exposes a light/dark theme switch through Mantine color schemes", () => {
    expect(clientEntry).toContain("useMantineColorScheme");
    expect(clientEntry).toContain("<Switch");
    expect(clientEntry).toContain("toggleColorScheme");
    expect(clientEntry).toContain("defaultColorScheme=\"light\"");
  });

  it("defines readable card and badge color treatments", () => {
    expect(styles).toContain(".project-card");
    expect(styles).toContain(".task-list-card");
    expect(styles).toContain(".compact-card");
    expect(styles).toContain(".label-badge");
    expect(styles).toContain("--app-text");
    expect(styles).toContain("background:");
  });

  it("defines app color tokens for light and dark themes", () => {
    expect(styles).toContain(":root,");
    expect(styles).toContain("[data-mantine-color-scheme=\"light\"]");
    expect(styles).toContain("[data-mantine-color-scheme=\"dark\"]");
    expect(styles).toContain("--app-surface");
    expect(styles).toContain("--app-surface-elevated");
    expect(styles).toContain("--app-border");
    expect(styles).toContain("--app-sidebar-bg");
  });

  it("places task IDs inline with list titles and in board card footers", () => {
    const compactCardBlock = clientEntry.match(/className="compact-card"[\s\S]*?<\/Card>/)?.[0] ?? "";

    expect(clientEntry).toContain("className=\"task-card-title-row\"");
    expect(clientEntry).toContain("className=\"board-card-task-id\"");
    expect(styles).toContain(".task-card-title-row");
    expect(styles).toContain(".board-card-task-id");
    expect(compactCardBlock).not.toContain("{columnLabels[column]}");
  });

  it("renders implementation branch names on board cards", () => {
    const compactCardBlock = clientEntry.match(/className="compact-card"[\s\S]*?<\/Card>/)?.[0] ?? "";

    expect(compactCardBlock).toContain("card.branchName");
    expect(compactCardBlock).toContain("className=\"board-card-branch\"");
    expect(styles).toContain(".board-card-branch");
  });
});
