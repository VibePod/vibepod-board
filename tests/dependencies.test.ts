import { describe, expect, it } from "vitest";

import {
  buildWorkOrder,
  dependencyMap,
  findCyclicTaskIds,
  isTaskComplete,
  wouldCreateCycle,
} from "../src/shared/dependencies.js";
import type { BoardCard, Idea } from "../src/shared/types.js";

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

const card = (
  patch: Partial<BoardCard> & Pick<BoardCard, "id" | "ideaId" | "column">,
): BoardCard => ({
  projectId: "project-1",
  title: "Card",
  details: "",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  createdAt: "2026-06-04T00:00:00.000Z",
  updatedAt: "2026-06-04T00:00:00.000Z",
  ...patch,
});

const projectKeys = new Map([["project-1", "APP"]]);

describe("task dependency graph", () => {
  it("treats done cards and denied tasks as complete", () => {
    expect(isTaskComplete({ status: "ready", column: "done" })).toBe(true);
    expect(isTaskComplete({ status: "denied" })).toBe(true);
    expect(isTaskComplete({ status: "ready", column: "in_progress" })).toBe(
      false,
    );
    expect(isTaskComplete({ status: "refining" })).toBe(false);
  });

  it("detects cycles before an edge is added", () => {
    const edges = dependencyMap([
      { id: "a", dependsOn: [] },
      { id: "b", dependsOn: ["a"] },
      { id: "c", dependsOn: ["b"] },
    ]);

    expect(wouldCreateCycle(edges, "a", "a")).toBe(true);
    expect(wouldCreateCycle(edges, "a", "c")).toBe(true);
    expect(wouldCreateCycle(edges, "a", "b")).toBe(true);
    expect(wouldCreateCycle(edges, "c", "a")).toBe(false);
  });

  it("reports tasks that sit in or behind a cycle", () => {
    const edges = dependencyMap([
      { id: "a", dependsOn: ["b"] },
      { id: "b", dependsOn: ["a"] },
      { id: "c", dependsOn: ["a"] },
      { id: "d", dependsOn: [] },
    ]);

    expect(findCyclicTaskIds(edges).sort()).toEqual(["a", "b", "c"]);
  });

  it("orders tasks so dependencies come first", () => {
    const order = buildWorkOrder(
      [
        task({
          id: "deploy",
          taskNumber: 3,
          title: "Deploy",
          dependsOn: ["api"],
        }),
        task({ id: "api", taskNumber: 2, title: "API", dependsOn: ["schema"] }),
        task({ id: "schema", taskNumber: 1, title: "Schema" }),
        task({ id: "docs", taskNumber: 4, title: "Docs" }),
      ],
      [],
      projectKeys,
    );

    expect(order.items.map((item) => item.id)).toEqual([
      "schema",
      "docs",
      "api",
      "deploy",
    ]);
    expect(order.items.map((item) => item.position)).toEqual([1, 2, 3, 4]);
    expect(order.items.map((item) => item.wave)).toEqual([0, 0, 1, 2]);
    expect(order.items[0].taskId).toBe("APP-1");
    expect(order.cyclicTaskIds).toEqual([]);
  });

  it("marks blocked tasks and clears them once the blocker is done", () => {
    const tasks = [
      task({ id: "schema", taskNumber: 1, title: "Schema" }),
      task({ id: "api", taskNumber: 2, title: "API", dependsOn: ["schema"] }),
    ];

    const open = buildWorkOrder(
      tasks,
      [card({ id: "card-schema", ideaId: "schema", column: "in_progress" })],
      projectKeys,
    );
    const blockedApi = open.items.find((item) => item.id === "api");
    expect(blockedApi?.blockedBy).toEqual(["schema"]);
    expect(blockedApi?.isBlocked).toBe(true);
    expect(blockedApi?.isActionable).toBe(false);
    expect(open.items.find((item) => item.id === "schema")?.isActionable).toBe(
      true,
    );

    const finished = buildWorkOrder(
      tasks,
      [card({ id: "card-schema", ideaId: "schema", column: "done" })],
      projectKeys,
    );
    const readyApi = finished.items.find((item) => item.id === "api");
    expect(readyApi?.blockedBy).toEqual([]);
    expect(readyApi?.isActionable).toBe(true);
    expect(
      finished.items.find((item) => item.id === "schema")?.isComplete,
    ).toBe(true);
  });

  it("keeps cyclic tasks out of the order and reports them", () => {
    const order = buildWorkOrder(
      [
        task({ id: "a", taskNumber: 1, title: "A", dependsOn: ["b"] }),
        task({ id: "b", taskNumber: 2, title: "B", dependsOn: ["a"] }),
        task({ id: "c", taskNumber: 3, title: "C" }),
      ],
      [],
      projectKeys,
    );

    expect(order.items.map((item) => item.id)).toEqual(["c"]);
    expect(order.cyclicTaskIds.sort()).toEqual(["a", "b"]);
  });

  it("ignores dependencies on tasks outside the requested scope", () => {
    const order = buildWorkOrder(
      [
        task({
          id: "api",
          taskNumber: 2,
          title: "API",
          dependsOn: ["missing"],
        }),
      ],
      [],
      projectKeys,
    );

    expect(order.items[0].blockedBy).toEqual([]);
    expect(order.items[0].dependsOn).toEqual(["missing"]);
    expect(order.items[0].isActionable).toBe(true);
  });
});
