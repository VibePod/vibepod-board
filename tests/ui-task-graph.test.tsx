// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TaskGraph } from "../src/client/TaskGraph.js";
import type { BoardColumn, Idea } from "../src/shared/types.js";

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

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
  task({
    id: "api",
    taskNumber: 2,
    title: "API",
    dependsOn: ["schema"],
    blockedBy: ["schema"],
  }),
];

const renderGraph = (
  items: Idea[],
  onOpenTask: (item: Idea) => void = vi.fn(),
  cardColumns: Map<string, BoardColumn> = new Map(),
) =>
  render(
    <MantineProvider>
      <TaskGraph
        tasks={items}
        projectKey="APP"
        cardColumns={cardColumns}
        onOpenTask={onOpenTask}
      />
    </MantineProvider>,
  );

describe("task dependency graph view", () => {
  it("renders one button per task with wave and blocked context", () => {
    renderGraph(tasks);

    expect(
      screen.getByRole("button", { name: "APP-1 Schema, wave 0. Open task." }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: "APP-2 API, wave 1, blocked. Open task.",
      }),
    ).toBeTruthy();
    expect(screen.getByText("Wave 0 · unblocked")).toBeTruthy();
    expect(screen.getByText("Wave 1")).toBeTruthy();
  });

  it("draws an edge per dependency", () => {
    const { container } = renderGraph(tasks);

    const edges = container.querySelectorAll("path.task-graph-edge");
    expect(edges).toHaveLength(1);
    expect(edges[0].classList.contains("blocking")).toBe(true);
  });

  it("marks edges from done blockers as satisfied", () => {
    const { container } = renderGraph(
      tasks,
      vi.fn(),
      new Map<string, BoardColumn>([["schema", "done"]]),
    );

    const edge = container.querySelector("path.task-graph-edge");
    expect(edge?.classList.contains("satisfied")).toBe(true);
  });

  it("opens the clicked task", async () => {
    const onOpenTask = vi.fn();
    renderGraph(tasks, onOpenTask);

    await userEvent.click(
      screen.getByRole("button", { name: "APP-1 Schema, wave 0. Open task." }),
    );

    expect(onOpenTask).toHaveBeenCalledWith(tasks[0]);
  });

  it("focuses the two connected tasks when an arrow is clicked", async () => {
    const { container } = renderGraph(tasks);
    const edge = screen.getByRole("button", {
      name: "APP-1 Schema blocks APP-2 API, still blocking. Focus these tasks.",
    });

    await userEvent.click(edge);

    expect(edge.getAttribute("aria-pressed")).toBe("true");
    expect(
      container
        .querySelector("path.task-graph-edge")
        ?.classList.contains("selected"),
    ).toBe(true);
    // Both endpoints stay lit, and the summary names them.
    expect(
      container.querySelectorAll('.task-graph-node[data-focused="true"]'),
    ).toHaveLength(2);
    expect(
      container.querySelectorAll('.task-graph-node[data-faded="true"]'),
    ).toHaveLength(0);
    expect(screen.getByText(/APP-1 Schema blocks APP-2 API/)).toBeTruthy();
  });

  it("fades the tasks the focused arrow does not touch", async () => {
    const { container } = renderGraph([
      ...tasks,
      task({ id: "docs", taskNumber: 3, title: "Docs" }),
    ]);

    await userEvent.click(
      screen.getByRole("button", {
        name: "APP-1 Schema blocks APP-2 API, still blocking. Focus these tasks.",
      }),
    );

    const faded = container.querySelectorAll(
      '.task-graph-node[data-faded="true"]',
    );
    expect(faded).toHaveLength(1);
    expect(faded[0].textContent).toContain("Docs");
  });

  it("fades the arrows that are not focused", async () => {
    const { container } = renderGraph([
      ...tasks,
      task({ id: "ui", taskNumber: 3, title: "UI", dependsOn: ["api"] }),
    ]);

    await userEvent.click(
      screen.getByRole("button", {
        name: "APP-1 Schema blocks APP-2 API, still blocking. Focus these tasks.",
      }),
    );

    expect(
      container.querySelectorAll('.task-graph-edge-group[data-faded="true"]'),
    ).toHaveLength(1);
  });

  it("clears the focus on a click away, on Escape, and on a second click", async () => {
    const { container } = renderGraph(tasks);
    const edge = screen.getByRole("button", {
      name: "APP-1 Schema blocks APP-2 API, still blocking. Focus these tasks.",
    });
    const focusedNodes = () =>
      container.querySelectorAll('.task-graph-node[data-focused="true"]')
        .length;

    await userEvent.click(edge);
    expect(focusedNodes()).toBe(2);
    await userEvent.click(edge);
    expect(focusedNodes()).toBe(0);

    await userEvent.click(edge);
    expect(focusedNodes()).toBe(2);
    await userEvent.keyboard("{Escape}");
    expect(focusedNodes()).toBe(0);

    await userEvent.click(edge);
    expect(focusedNodes()).toBe(2);
    await userEvent.click(screen.getByText(/Blockers point to the tasks/));
    expect(focusedNodes()).toBe(0);
  });

  it("focuses an arrow from the keyboard", async () => {
    const { container } = renderGraph(tasks);
    const edge = screen.getByRole("button", {
      name: "APP-1 Schema blocks APP-2 API, still blocking. Focus these tasks.",
    });

    edge.focus();
    await userEvent.keyboard("{Enter}");

    expect(
      container.querySelectorAll('.task-graph-node[data-focused="true"]'),
    ).toHaveLength(2);
  });

  it("warns about tasks stuck in a cycle", () => {
    const { container } = renderGraph([
      task({
        id: "loop-a",
        taskNumber: 1,
        title: "Loop A",
        dependsOn: ["loop-b"],
      }),
      task({
        id: "loop-b",
        taskNumber: 2,
        title: "Loop B",
        dependsOn: ["loop-a"],
      }),
    ]);

    expect(screen.getByText(/cannot be ordered/)).toBeTruthy();
    expect(
      container.querySelector(".task-graph-lane-label.cycle")?.textContent,
    ).toBe("Cycle");
  });
});
