import { describe, expect, it } from "vitest";

import { taskNavigationFor } from "../src/client/taskNavigation.js";
import type { Idea } from "../src/shared/types.js";

const task = (id: string): Idea => ({
  id,
  projectId: "project-1",
  taskNumber: 1,
  title: id,
  summary: "",
  details: "",
  status: "idea",
  labels: [],
  acceptanceCriteria: [],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  createdAt: "2026-01-01T00:00:00.000Z",
  updatedAt: "2026-01-01T00:00:00.000Z",
});

const tasks = [task("a"), task("b"), task("c")];

describe("task modal navigation", () => {
  it("reports the position and both neighbours of the current task", () => {
    const navigation = taskNavigationFor(tasks, "b");

    expect(navigation).toMatchObject({ index: 1, total: 3 });
    expect(navigation?.previous.id).toBe("a");
    expect(navigation?.next.id).toBe("c");
  });

  it("wraps around at both ends of the list", () => {
    expect(taskNavigationFor(tasks, "a")?.previous.id).toBe("c");
    expect(taskNavigationFor(tasks, "c")?.next.id).toBe("a");
  });

  it("points a single task at itself", () => {
    const navigation = taskNavigationFor([task("only")], "only");

    expect(navigation).toMatchObject({ index: 0, total: 1 });
    expect(navigation?.previous.id).toBe("only");
    expect(navigation?.next.id).toBe("only");
  });

  it("has no navigation without a current task or for unknown tasks", () => {
    expect(taskNavigationFor(tasks, undefined)).toBeNull();
    expect(taskNavigationFor(tasks, "missing")).toBeNull();
    expect(taskNavigationFor([], "a")).toBeNull();
  });
});
