import { describe, expect, it } from "vitest";

import {
  navigationForProjectSelection,
  projectSelectorOptions,
  shouldShowProjectSidebar
} from "../src/client/layoutNavigation.js";
import type { Project } from "../src/shared/types.js";

const project = (id: string, title: string): Project => ({
  id,
  key: title.slice(0, 3).toUpperCase(),
  title,
  summary: "",
  createdAt: "2026-06-04T00:00:00.000Z",
  updatedAt: "2026-06-04T00:00:00.000Z"
});

describe("layout navigation helpers", () => {
  it("opens selected projects on tasks from the home view", () => {
    expect(navigationForProjectSelection("projects", "project-1")).toEqual({
      activeView: "ideas",
      selectedProjectId: "project-1"
    });
  });

  it("keeps the current project section when switching projects", () => {
    expect(navigationForProjectSelection("board", "project-2")).toEqual({
      activeView: "board",
      selectedProjectId: "project-2"
    });
  });

  it("shows project side navigation only inside a valid project", () => {
    expect(shouldShowProjectSidebar("projects", false)).toBe(false);
    expect(shouldShowProjectSidebar("ideas", false)).toBe(false);
    expect(shouldShowProjectSidebar("ideas", true)).toBe(true);
  });

  it("maps projects into selector options", () => {
    expect(projectSelectorOptions([project("project-1", "Website"), project("project-2", "API")])).toEqual([
      { value: "project-1", label: "WEB - Website" },
      { value: "project-2", label: "API - API" }
    ]);
  });
});
