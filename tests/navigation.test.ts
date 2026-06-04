import { describe, expect, it } from "vitest";

import { formatNavigationPath, parseNavigationPath } from "../src/client/navigation.js";

describe("client navigation URLs", () => {
  it("parses the projects homepage URL", () => {
    expect(parseNavigationPath("/")).toEqual({ activeView: "projects", selectedProjectId: "" });
    expect(parseNavigationPath("/projects")).toEqual({
      activeView: "projects",
      selectedProjectId: ""
    });
  });

  it("parses project task, board, and notes URLs", () => {
    expect(parseNavigationPath("/projects/project-1/tasks")).toEqual({
      activeView: "ideas",
      selectedProjectId: "project-1"
    });
    expect(parseNavigationPath("/projects/project-1/board")).toEqual({
      activeView: "board",
      selectedProjectId: "project-1"
    });
    expect(parseNavigationPath("/projects/project-1/notes")).toEqual({
      activeView: "documents",
      selectedProjectId: "project-1"
    });
  });

  it("formats shareable project URLs", () => {
    expect(formatNavigationPath({ activeView: "projects", selectedProjectId: "" })).toBe("/projects");
    expect(formatNavigationPath({ activeView: "ideas", selectedProjectId: "project 1" })).toBe(
      "/projects/project%201/tasks"
    );
    expect(formatNavigationPath({ activeView: "board", selectedProjectId: "project-1" })).toBe(
      "/projects/project-1/board"
    );
    expect(formatNavigationPath({ activeView: "documents", selectedProjectId: "project-1" })).toBe(
      "/projects/project-1/notes"
    );
  });
});
