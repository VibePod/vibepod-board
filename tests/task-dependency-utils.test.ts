import { describe, expect, it } from "vitest";

import {
  dependencyLinks,
  dependencyOptions,
} from "../src/client/taskDependencyUtils.js";
import { filterAndSortTasks } from "../src/client/taskListUtils.js";
import type { Idea } from "../src/shared/types.js";

const task = (
  patch: Partial<Idea> & Pick<Idea, "id" | "taskNumber" | "title">,
): Idea => ({
  projectId: "project-1",
  summary: "",
  details: "",
  status: "ready",
  labels: [],
  acceptanceCriteria: [],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  createdAt: "2026-06-04T00:00:00.000Z",
  updatedAt: "2026-06-04T00:00:00.000Z",
  ...patch,
});

const tasks = [
  task({ id: "schema", taskNumber: 1, title: "Schema" }),
  task({ id: "api", taskNumber: 2, title: "API", dependsOn: ["schema"] }),
  task({ id: "deploy", taskNumber: 3, title: "Deploy", dependsOn: ["api"] }),
];

describe("task dependency utilities", () => {
  it("offers every other task when creating a task", () => {
    expect(dependencyOptions(tasks, "APP")).toEqual([
      { value: "schema", label: "APP-1 · Schema" },
      { value: "api", label: "APP-2 · API" },
      { value: "deploy", label: "APP-3 · Deploy" },
    ]);
  });

  it("hides the task itself and anything depending on it", () => {
    expect(dependencyOptions(tasks, "APP", "schema")).toEqual([]);
    expect(
      dependencyOptions(tasks, "APP", "deploy").map((option) => option.value),
    ).toEqual(["schema", "api"]);
  });

  it("resolves dependency ids into displayable links", () => {
    const tasksById = new Map(tasks.map((item) => [item.id, item]));

    expect(
      dependencyLinks(["api", "schema"], tasksById, "APP", ["api"]),
    ).toEqual([
      {
        id: "schema",
        taskId: "APP-1",
        title: "Schema",
        status: "ready",
        isBlocking: false,
      },
      {
        id: "api",
        taskId: "APP-2",
        title: "API",
        status: "ready",
        isBlocking: true,
      },
    ]);
    expect(dependencyLinks(["gone"], tasksById, "APP")).toEqual([]);
  });

  it("sorts the task list in dependency order", () => {
    const workOrder = new Map([
      ["schema", 1],
      ["api", 2],
      ["deploy", 3],
    ]);

    expect(
      filterAndSortTasks([...tasks].reverse(), {
        sort: "dependency_asc",
        workOrder,
      }).map((item) => item.id),
    ).toEqual(["schema", "api", "deploy"]);
  });

  it("parks tasks without a work-order position last", () => {
    const workOrder = new Map([["deploy", 1]]);

    expect(
      filterAndSortTasks(tasks, {
        sort: "dependency_asc",
        workOrder,
      }).map((item) => item.id),
    ).toEqual(["deploy", "schema", "api"]);
  });
});
