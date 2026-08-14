import { describe, expect, it } from "vitest";

import {
  buildTaskGraphLayout,
  COLUMN_GAP,
  LANE_LABEL_HEIGHT,
  NODE_HEIGHT,
  NODE_WIDTH,
  PADDING,
  ROW_GAP,
} from "../src/client/taskGraphLayout.js";
import type { BoardColumn, Idea } from "../src/shared/types.js";

/** "M x y C ..." — the point an edge leaves its blocker at. */
const startPoint = (path: string) => {
  const [x, y] = path.slice(2, path.indexOf(" C")).split(" ").map(Number);
  return { x, y };
};

/** "... , x y" — the point an edge arrives at its dependent. */
const endPoint = (path: string) => {
  const [x, y] = path
    .slice(path.lastIndexOf(",") + 2)
    .split(" ")
    .map(Number);
  return { x, y };
};

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

const chain = [
  task({ id: "schema", taskNumber: 1, title: "Schema", blocks: ["api"] }),
  task({
    id: "api",
    taskNumber: 2,
    title: "API",
    dependsOn: ["schema"],
    blockedBy: ["schema"],
    blocks: ["deploy"],
  }),
  task({
    id: "deploy",
    taskNumber: 3,
    title: "Deploy",
    dependsOn: ["api"],
    blockedBy: ["api"],
  }),
];

const layoutOf = (tasks: Idea[], cardColumns?: Map<string, BoardColumn>) =>
  buildTaskGraphLayout(tasks, {
    projectKey: "APP",
    ...(cardColumns ? { cardColumns } : {}),
  });

const nodeById = (tasks: Idea[], id: string) => {
  const node = layoutOf(tasks).nodes.find((item) => item.id === id);
  if (!node) {
    throw new Error(`node ${id} was not placed`);
  }
  return node;
};

describe("task graph layout", () => {
  it("places a dependency chain into one lane per wave", () => {
    const layout = layoutOf(chain);

    expect(
      layout.nodes.map((node) => [node.taskId, node.wave, node.row]),
    ).toEqual([
      ["APP-1", 0, 0],
      ["APP-2", 1, 0],
      ["APP-3", 2, 0],
    ]);
    expect(layout.lanes.map((lane) => lane.label)).toEqual([
      "Wave 0 · unblocked",
      "Wave 1",
      "Wave 2",
    ]);
    expect(layout.hasCycle).toBe(false);
  });

  it("spreads tasks of the same wave over rows and sizes the canvas", () => {
    const layout = layoutOf([
      task({ id: "a", taskNumber: 1, title: "A" }),
      task({ id: "b", taskNumber: 2, title: "B" }),
      task({ id: "c", taskNumber: 3, title: "C", dependsOn: ["a"] }),
    ]);

    expect(layout.nodes.map((node) => [node.id, node.wave, node.row])).toEqual([
      ["a", 0, 0],
      ["b", 0, 1],
      ["c", 1, 0],
    ]);
    // Two lanes wide, two rows tall, including padding and the lane labels.
    expect(layout.width).toBe(
      PADDING + NODE_WIDTH + COLUMN_GAP + NODE_WIDTH + PADDING,
    );
    expect(layout.height).toBe(
      PADDING +
        LANE_LABEL_HEIGHT +
        NODE_HEIGHT +
        ROW_GAP +
        NODE_HEIGHT +
        PADDING,
    );
    expect(layout.nodeWidth).toBe(NODE_WIDTH);
    expect(layout.nodeHeight).toBe(NODE_HEIGHT);
  });

  it("orders a lane next to the blockers it waits for", () => {
    const layout = layoutOf([
      task({ id: "top", taskNumber: 1, title: "Top" }),
      task({ id: "bottom", taskNumber: 2, title: "Bottom" }),
      // The lower-numbered dependent waits on the lower blocker, so row order
      // follows the blockers rather than the task numbers.
      task({
        id: "early",
        taskNumber: 3,
        title: "Early",
        dependsOn: ["bottom"],
      }),
      task({ id: "late", taskNumber: 4, title: "Late", dependsOn: ["top"] }),
    ]);

    expect(
      layout.nodes
        .filter((node) => node.wave === 1)
        .map((node) => [node.id, node.row]),
    ).toEqual([
      ["late", 0],
      ["early", 1],
    ]);
  });

  it("draws one edge per dependency from blocker to dependent", () => {
    const layout = layoutOf(chain);

    expect(layout.edges.map((edge) => [edge.fromId, edge.toId])).toEqual([
      ["schema", "api"],
      ["api", "deploy"],
    ]);
    const [first] = layout.edges;
    const centerY = PADDING + LANE_LABEL_HEIGHT + NODE_HEIGHT / 2;
    // A lone edge leaves the blocker's right edge and arrives at the dependent's
    // left edge, both at mid-height.
    expect(first.path.startsWith(`M ${PADDING + NODE_WIDTH} ${centerY}`)).toBe(
      true,
    );
    expect(
      first.path.endsWith(`${PADDING + NODE_WIDTH + COLUMN_GAP} ${centerY}`),
    ).toBe(true);
    expect(first.isBlocking).toBe(true);
  });

  it("fans edges out over the node side instead of stacking them", () => {
    const layout = layoutOf([
      task({ id: "one", taskNumber: 1, title: "One" }),
      task({ id: "two", taskNumber: 2, title: "Two" }),
      task({ id: "three", taskNumber: 3, title: "Three" }),
      task({
        id: "join",
        taskNumber: 4,
        title: "Join",
        dependsOn: ["one", "two", "three"],
      }),
    ]);

    const arrivals = layout.edges.map((edge) => endPoint(edge.path));
    // Three distinct arrival points, ordered like the blockers they come from.
    expect(new Set(arrivals.map((point) => point.y)).size).toBe(3);
    expect(arrivals.map((point) => point.y)).toEqual(
      [...arrivals.map((point) => point.y)].sort((a, b) => a - b),
    );
    // All anchors stay on the node's left edge, inside its height.
    const join = layout.nodes.find((node) => node.id === "join");
    for (const point of arrivals) {
      expect(point.x).toBe(join?.x);
      expect(point.y).toBeGreaterThan(join?.y ?? 0);
      expect(point.y).toBeLessThan((join?.y ?? 0) + NODE_HEIGHT);
    }
  });

  it("fans edges leaving a shared blocker as well", () => {
    const layout = layoutOf([
      task({ id: "root", taskNumber: 1, title: "Root" }),
      task({ id: "left", taskNumber: 2, title: "Left", dependsOn: ["root"] }),
      task({ id: "right", taskNumber: 3, title: "Right", dependsOn: ["root"] }),
    ]);

    const departures = layout.edges.map((edge) => startPoint(edge.path));
    expect(new Set(departures.map((point) => point.y)).size).toBe(2);
  });

  it("marks edges from finished blockers as satisfied", () => {
    const cardColumns = new Map<string, BoardColumn>([["schema", "done"]]);
    const layout = layoutOf(chain, cardColumns);

    expect(layout.edges.map((edge) => [edge.fromId, edge.isBlocking])).toEqual([
      ["schema", false],
      ["api", true],
    ]);
    expect(layout.nodes[0].isComplete).toBe(true);
  });

  it("treats denied tasks as finished blockers", () => {
    const layout = layoutOf([
      task({
        id: "dropped",
        taskNumber: 1,
        title: "Dropped",
        status: "denied",
      }),
      task({
        id: "next",
        taskNumber: 2,
        title: "Next",
        dependsOn: ["dropped"],
      }),
    ]);

    expect(layout.edges[0].isBlocking).toBe(false);
  });

  it("counts dependencies on tasks the filters left out", () => {
    const filtered = chain.filter((item) => item.id !== "schema");
    const layout = layoutOf(filtered);

    expect(layout.nodes.map((node) => [node.id, node.wave])).toEqual([
      ["api", 0],
      ["deploy", 1],
    ]);
    expect(layout.edges.map((edge) => edge.id)).toEqual(["api->deploy"]);
    expect(nodeById(filtered, "api").hiddenDependencyCount).toBe(1);
    expect(nodeById(filtered, "deploy").hiddenDependencyCount).toBe(0);
  });

  it("keeps the blocked flag from the full graph even when filtered", () => {
    const filtered = chain.filter((item) => item.id !== "schema");

    // "api" still waits for the hidden blocker, so it must not look actionable.
    expect(nodeById(filtered, "api").isBlocked).toBe(true);
  });

  it("parks tasks in a cycle in a trailing lane", () => {
    const tasks = [
      task({ id: "start", taskNumber: 1, title: "Start" }),
      task({
        id: "loop-a",
        taskNumber: 2,
        title: "Loop A",
        dependsOn: ["loop-b"],
      }),
      task({
        id: "loop-b",
        taskNumber: 3,
        title: "Loop B",
        dependsOn: ["loop-a"],
      }),
    ];
    const layout = layoutOf(tasks);

    expect(layout.hasCycle).toBe(true);
    expect(layout.lanes.map((lane) => [lane.label, lane.nodeCount])).toEqual([
      ["Wave 0 · unblocked", 1],
      ["Cycle", 2],
    ]);
    expect(
      layout.nodes
        .filter((node) => node.isCyclic)
        .map((node) => [node.id, node.wave]),
    ).toEqual([
      ["loop-a", 1],
      ["loop-b", 1],
    ]);
    // Both directions of the loop are still drawn.
    expect(layout.edges.map((edge) => edge.id).sort()).toEqual([
      "loop-a->loop-b",
      "loop-b->loop-a",
    ]);
  });

  it("returns an empty canvas without tasks", () => {
    const layout = layoutOf([]);

    expect(layout).toMatchObject({
      nodes: [],
      edges: [],
      lanes: [],
      width: 0,
      height: 0,
      hasCycle: false,
    });
  });
});
