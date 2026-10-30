import { describe, expect, it } from "vitest";

import {
  formatNavigationPath,
  formatNavigationUrl,
  parseBoardSearch,
  parseNavigationPath,
} from "../src/client/navigation.js";

describe("client navigation URLs", () => {
  it("parses the projects homepage URL", () => {
    expect(parseNavigationPath("/")).toEqual({
      activeView: "projects",
      selectedProjectId: "",
    });
    expect(parseNavigationPath("/projects")).toEqual({
      activeView: "projects",
      selectedProjectId: "",
    });
  });

  it("parses project task, board, and notes URLs", () => {
    expect(parseNavigationPath("/projects/project-1/tasks")).toEqual({
      activeView: "ideas",
      selectedProjectId: "project-1",
    });
    expect(parseNavigationPath("/projects/project-1/board")).toEqual({
      activeView: "board",
      selectedProjectId: "project-1",
    });
    expect(parseNavigationPath("/projects/project-1/notes")).toEqual({
      activeView: "documents",
      selectedProjectId: "project-1",
    });
  });

  it("parses the project archive URL", () => {
    expect(parseNavigationPath("/projects/project-1/archive")).toEqual({
      activeView: "archive",
      selectedProjectId: "project-1",
    });
    expect(parseNavigationPath("/projects/project%201/archive/")).toEqual({
      activeView: "archive",
      selectedProjectId: "project 1",
    });
  });

  it("formats and round-trips the project archive URL", () => {
    const navigation = {
      activeView: "archive" as const,
      selectedProjectId: "project 1",
    };
    expect(formatNavigationPath(navigation)).toBe(
      "/projects/project%201/archive",
    );
    expect(parseNavigationPath(formatNavigationPath(navigation))).toEqual(
      navigation,
    );
  });

  it("formats shareable project URLs", () => {
    expect(
      formatNavigationPath({ activeView: "projects", selectedProjectId: "" }),
    ).toBe("/projects");
    expect(
      formatNavigationPath({
        activeView: "ideas",
        selectedProjectId: "project 1",
      }),
    ).toBe("/projects/project%201/tasks");
    expect(
      formatNavigationPath({
        activeView: "board",
        selectedProjectId: "project-1",
      }),
    ).toBe("/projects/project-1/board");
    expect(
      formatNavigationPath({
        activeView: "documents",
        selectedProjectId: "project-1",
      }),
    ).toBe("/projects/project-1/notes");
  });
});

describe("archived projects URLs", () => {
  it("parses the archived projects list and one archived project", () => {
    expect(parseNavigationPath("/archived-projects")).toEqual({
      activeView: "archivedProjects",
      selectedProjectId: "",
    });
    expect(parseNavigationPath("/archived-projects/project%201/")).toEqual({
      activeView: "archivedProjects",
      selectedProjectId: "project 1",
    });
  });

  it("formats and round-trips archived project URLs", () => {
    const list = {
      activeView: "archivedProjects",
      selectedProjectId: "",
    } as const;
    const one = { ...list, selectedProjectId: "project 1" };
    expect(formatNavigationPath(list)).toBe("/archived-projects");
    expect(formatNavigationPath(one)).toBe("/archived-projects/project%201");
    expect(parseNavigationPath(formatNavigationPath(one))).toEqual(one);
    expect(formatNavigationUrl(one, "ignored")).toBe(
      "/archived-projects/project%201",
    );
  });
});

describe("board search URLs", () => {
  it("reads the board search from the q parameter", () => {
    expect(parseBoardSearch("?q=VP-85")).toBe("VP-85");
    expect(parseBoardSearch("?q=launch%20page")).toBe("launch page");
    expect(parseBoardSearch("?q=%2385")).toBe("#85");
    expect(parseBoardSearch("")).toBe("");
    expect(parseBoardSearch("?other=1")).toBe("");
  });

  it("adds a non-blank board search to board URLs only", () => {
    const board = { activeView: "board", selectedProjectId: "p-1" } as const;
    expect(formatNavigationUrl(board, "#85")).toBe(
      "/projects/p-1/board?q=%2385",
    );
    expect(formatNavigationUrl(board, "launch page")).toBe(
      "/projects/p-1/board?q=launch+page",
    );
    expect(formatNavigationUrl(board, "  ")).toBe("/projects/p-1/board");
    expect(formatNavigationUrl(board)).toBe("/projects/p-1/board");
    expect(
      formatNavigationUrl({ ...board, activeView: "ideas" }, "VP-85"),
    ).toBe("/projects/p-1/tasks");
  });
});
