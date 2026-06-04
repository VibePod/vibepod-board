import { describe, expect, it } from "vitest";

import { filterAndSortTasks } from "../src/client/taskListUtils.js";
import type { Idea } from "../src/shared/types.js";

const task = (patch: Partial<Idea> & Pick<Idea, "id" | "title" | "createdAt">): Idea => ({
  projectId: "project-1",
  taskNumber: 1,
  summary: "",
  details: "",
  status: "idea",
  labels: [],
  acceptanceCriteria: [],
  updatedAt: patch.createdAt,
  ...patch
});

describe("task list utilities", () => {
  it("sorts tasks by latest created first by default", () => {
    const tasks = [
      task({ id: "old", title: "Old task", createdAt: "2026-01-01T00:00:00.000Z" }),
      task({ id: "new", title: "New task", createdAt: "2026-02-01T00:00:00.000Z" })
    ];

    expect(filterAndSortTasks(tasks, {}).map((item) => item.id)).toEqual(["new", "old"]);
  });

  it("filters by status, label, and search text", () => {
    const tasks = [
      task({
        id: "match",
        title: "Publish launch page",
        summary: "Marketing release",
        status: "ready",
        labels: ["frontend"],
        createdAt: "2026-02-01T00:00:00.000Z"
      }),
      task({
        id: "wrong-label",
        title: "Publish API",
        status: "ready",
        labels: ["backend"],
        createdAt: "2026-03-01T00:00:00.000Z"
      }),
      task({
        id: "wrong-status",
        title: "Publish launch page",
        status: "idea",
        labels: ["frontend"],
        createdAt: "2026-04-01T00:00:00.000Z"
      })
    ];

    expect(
      filterAndSortTasks(tasks, {
        status: "ready",
        label: "frontend",
        search: "launch"
      }).map((item) => item.id)
    ).toEqual(["match"]);
  });

  it("supports title sorting", () => {
    const tasks = [
      task({ id: "b", title: "Beta", createdAt: "2026-01-01T00:00:00.000Z" }),
      task({ id: "a", title: "Alpha", createdAt: "2026-02-01T00:00:00.000Z" })
    ];

    expect(filterAndSortTasks(tasks, { sort: "title_asc" }).map((item) => item.id)).toEqual(["a", "b"]);
  });
});
