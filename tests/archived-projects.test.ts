import { describe, expect, it } from "vitest";

import {
  activeProjects,
  formatProjectCounts,
  isProjectKeyConfirmed,
  nonEmptyColumns,
  projectDeletionConsequences,
  sortArchivedProjects,
} from "../src/client/archivedProjects.js";
import type { BoardCard, Project } from "../src/shared/types.js";

const project = (key: string, archivedAt?: string | null): Project => ({
  id: key.toLowerCase(),
  key,
  title: key,
  summary: "",
  archivedAt,
  createdAt: "2026-06-04T00:00:00.000Z",
  updatedAt: "2026-06-04T00:00:00.000Z",
});

describe("archived projects", () => {
  it("leaves archived projects out of the active ones", () => {
    const projects = [
      project("APP"),
      project("OLD", "2026-10-01T00:00:00.000Z"),
      project("NEW", null),
    ];
    expect(activeProjects(projects).map((p) => p.key)).toEqual(["APP", "NEW"]);
  });

  it("lists archived projects, most recently archived first", () => {
    const projects = [
      project("APP"),
      project("OLD", "2026-09-01T00:00:00.000Z"),
      project("NEW", "2026-10-01T00:00:00.000Z"),
      project("ALT", "2026-10-01T00:00:00.000Z"),
    ];
    expect(sortArchivedProjects(projects).map((p) => p.key)).toEqual([
      "ALT",
      "NEW",
      "OLD",
    ]);
  });

  it("confirms a delete only with the project key, in any case", () => {
    expect(isProjectKeyConfirmed("APP", "APP")).toBe(true);
    expect(isProjectKeyConfirmed(" app ", "APP")).toBe(true);
    expect(isProjectKeyConfirmed("AP", "APP")).toBe(false);
    expect(isProjectKeyConfirmed("", "APP")).toBe(false);
  });

  it("names the counts and what a delete removes", () => {
    const counted = { ...project("APP"), taskCount: 1, cardCount: 3 };
    expect(formatProjectCounts(counted)).toBe("1 task, 3 cards");
    expect(formatProjectCounts(project("APP"))).toBe("0 tasks, 0 cards");
    expect(projectDeletionConsequences(counted)[0]).toContain(
      "1 task, 3 cards",
    );
    expect(projectDeletionConsequences(counted).join(" ")).toContain(
      "cannot be undone",
    );
  });

  it("keeps the board's non-empty columns in board order", () => {
    const card = { id: "c" } as BoardCard;
    expect(
      nonEmptyColumns({
        ready: [],
        planned: [],
        in_progress: [],
        review: [],
        pr_ready: [],
        done: [card],
      }).map(({ column }) => column),
    ).toEqual(["done"]);
  });
});
