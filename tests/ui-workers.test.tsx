// @vitest-environment jsdom

import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  BoardCard,
  BoardColumns,
  Idea,
  Worker,
} from "../src/shared/types.js";

const timestamp = "2026-09-29T08:00:00.000Z";

const project = {
  id: "project-1",
  key: "VP",
  title: "VibePod",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};

const idea = (patch: Partial<Idea>): Idea => ({
  id: "idea-1",
  projectId: project.id,
  taskNumber: 1,
  title: "Task",
  summary: "",
  details: "",
  status: "ready",
  labels: [],
  acceptanceCriteria: [],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const card = (patch: Partial<BoardCard>): BoardCard => ({
  id: "card-1",
  projectId: project.id,
  ideaId: "idea-1",
  title: "Task",
  details: "",
  column: "in_progress",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const worker = (patch: Partial<Worker>): Worker => ({
  id: "worker-1",
  projectId: project.id,
  name: "claude@laptop",
  agent: "claude",
  machine: "laptop",
  status: "idle",
  startedAt: timestamp,
  lastSeenAt: new Date().toISOString(),
  ...patch,
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

type Scenario = { workers: Worker[]; columns: BoardColumns };

const boardApi = (scenario: Scenario) =>
  vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/auth/me") {
      return jsonResponse({ authenticated: true, username: "admin" });
    }
    if (path === "/api/projects") return jsonResponse({ items: [project] });
    if (path === "/api/ideas") {
      return jsonResponse({
        items: [
          idea({ id: "idea-1", taskNumber: 1, title: "Automated login" }),
          idea({ id: "idea-2", taskNumber: 2, title: "Manual logout" }),
        ],
      });
    }
    if (path === "/api/documents") return jsonResponse({ items: [] });
    if (path === "/api/board")
      return jsonResponse({ columns: scenario.columns });
    if (path === "/api/board/archived") return jsonResponse({ items: [] });
    if (path === `/api/workers?projectId=${project.id}`) {
      return jsonResponse({ items: scenario.workers });
    }
    return jsonResponse({ error: "Not found" }, 404);
  });

const inProgress = (): BoardColumns => ({
  ready: [],
  planned: [],
  in_progress: [
    card({ id: "card-1", ideaId: "idea-1", title: "Automated login" }),
    card({ id: "card-2", ideaId: "idea-2", title: "Manual logout" }),
  ],
  review: [],
  done: [],
});

const renderBoard = async (scenario: Scenario) => {
  window.history.replaceState(null, "", `/projects/${project.id}/board`);
  const fetchMock = boardApi(scenario);
  vi.stubGlobal("fetch", fetchMock);
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  await screen.findByText("Automated login");
  return fetchMock;
};

const cardFor = (title: string) =>
  screen.getByText(title).closest(".compact-card") as HTMLElement;

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
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
  class ResizeObserverStub {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("connected workers", () => {
  it("shows a live indicator on the card being worked on", async () => {
    await renderBoard({
      workers: [
        worker({
          status: "working",
          taskId: "idea-1",
          taskKey: "VP-1",
          taskTitle: "Automated login",
          step: "agent_running",
          taskStartedAt: new Date(Date.now() - 125_000).toISOString(),
        }),
      ],
      columns: inProgress(),
    });

    const automated = cardFor("Automated login");
    const indicator = await within(automated).findByLabelText(
      "Being worked on by claude@laptop",
    );
    expect(within(indicator).getByText("Agent running")).toBeTruthy();
    expect(
      indicator.querySelector(".board-card-worker-elapsed")?.textContent,
    ).toMatch(/^2:0\d$/);
    expect(
      cardFor("Manual logout").querySelector(".board-card-worker"),
    ).toBeNull();
  });

  it("drops the indicator once the card left In Progress", async () => {
    const columns = inProgress();
    const [moved] = columns.in_progress.splice(0, 1);
    columns.review.push({ ...moved, column: "review" });
    await renderBoard({
      workers: [worker({ status: "working", taskId: "idea-1" })],
      columns,
    });

    await screen.findByRole("button", { name: /1 worker/ });
    expect(document.querySelector(".board-card-worker")).toBeNull();
  });

  it("drops the indicator for an offline worker", async () => {
    await renderBoard({
      workers: [worker({ status: "offline", taskId: "idea-1" })],
      columns: inProgress(),
    });

    await screen.findByRole("button", { name: "Workers offline" });
    expect(document.querySelector(".board-card-worker")).toBeNull();
  });

  it("lists workers with their status from the project header", async () => {
    await renderBoard({
      workers: [
        worker({
          id: "worker-1",
          status: "working",
          taskId: "idea-1",
          taskKey: "VP-1",
          taskTitle: "Automated login",
          step: "verifying",
          taskStartedAt: timestamp,
        }),
        worker({
          id: "worker-2",
          name: "codex@desktop",
          agent: "codex",
          machine: "desktop",
          status: "paused",
          statusReason: "Usage limit reached",
        }),
      ],
      columns: inProgress(),
    });

    await userEvent.click(
      await screen.findByRole("button", { name: "2 workers · 1 working" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Workers" });
    const rows = within(dialog).getAllByText(/@/);
    expect(rows.map((row) => row.textContent)).toEqual([
      "claude@laptop",
      "codex@desktop",
    ]);
    expect(within(dialog).getByText("Usage limit reached")).toBeTruthy();
    expect(within(dialog).getByText("Paused")).toBeTruthy();
    expect(within(dialog).getByText("Verifying")).toBeTruthy();

    await userEvent.click(
      within(dialog).getByRole("button", { name: "VP-1 Automated login" }),
    );
    expect(
      await screen.findByRole("dialog", { name: "Task Overview" }),
    ).toBeTruthy();
  });

  it("reloads the board when a worker moves on", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const scenario: Scenario = {
        workers: [worker({ status: "working", taskId: "idea-1" })],
        columns: inProgress(),
      };
      const fetchMock = await renderBoard(scenario);
      const boardLoads = () =>
        fetchMock.mock.calls.filter(([input]) => String(input) === "/api/board")
          .length;
      await screen.findByRole("button", { name: /1 working/ });
      const before = boardLoads();

      // A heartbeat that only changes the step leaves the board alone.
      scenario.workers = [
        worker({ status: "working", taskId: "idea-1", step: "verifying" }),
      ];
      await vi.advanceTimersByTimeAsync(5000);
      expect(boardLoads()).toBe(before);

      // Handing the task over does not.
      scenario.workers = [worker({ status: "idle" })];
      await vi.advanceTimersByTimeAsync(5000);
      await waitFor(() => expect(boardLoads()).toBe(before + 1));
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps an error on screen when the board reloads in the background", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const columns = inProgress();
      columns.done.push(
        card({
          id: "card-9",
          ideaId: "idea-2",
          title: "Shipped",
          column: "done",
        }),
      );
      const scenario: Scenario = {
        workers: [worker({ status: "working", taskId: "idea-1" })],
        columns,
      };
      await renderBoard(scenario);
      await screen.findByRole("button", { name: /1 working/ });

      await userEvent.click(
        screen.getByRole("button", { name: "Archive Shipped" }),
      );
      expect(await screen.findByText("Not found")).toBeTruthy();

      scenario.workers = [worker({ status: "idle" })];
      await vi.advanceTimersByTimeAsync(5000);
      await screen.findByRole("button", { name: "1 worker" });
      expect(screen.getByText("Not found")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("offers Stop for a silent worker that never signed off", async () => {
    await renderBoard({
      workers: [
        worker({ id: "silent", name: "silent@box", status: "offline" }),
        worker({
          id: "gone",
          name: "gone@box",
          status: "offline",
          stoppedAt: timestamp,
        }),
      ],
      columns: inProgress(),
    });

    await userEvent.click(
      await screen.findByRole("button", { name: "Workers offline" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Workers" });
    expect(
      within(dialog).getByRole("button", { name: "Stop silent@box" }),
    ).toBeTruthy();
    expect(
      within(dialog).queryByRole("button", { name: "Stop gone@box" }),
    ).toBeNull();
  });

  it("hides the header indicator when no worker was seen", async () => {
    const fetchMock = await renderBoard({ workers: [], columns: inProgress() });

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).startsWith("/api/workers"),
        ),
      ).toBe(true),
    );
    expect(document.querySelector(".workers-indicator")).toBeNull();
  });

  it("keeps the newest board when an older reload finishes last", async () => {
    const scenario = { workers: [], columns: inProgress() };
    const fetchMock = await renderBoard(scenario);
    const baseline = fetchMock.getMockImplementation();
    let releaseStale: (() => void) | undefined;
    const renamed = (title: string): BoardColumns => ({
      ...inProgress(),
      in_progress: [
        card({ id: "card-1", ideaId: "idea-1", title }),
        card({ id: "card-2", ideaId: "idea-2", title: "Manual logout" }),
      ],
    });
    let boardLoads = 0;
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/board") {
        boardLoads += 1;
        if (boardLoads === 1) {
          await new Promise<void>((resolve) => {
            releaseStale = resolve;
          });
          return jsonResponse({ columns: renamed("Stale snapshot") });
        }
        return jsonResponse({ columns: renamed("Newest snapshot") });
      }
      return baseline?.(input) ?? jsonResponse({ error: "Not found" }, 404);
    });

    const refresh = screen.getByRole("button", { name: "Refresh" });
    fireEvent.click(refresh);
    await waitFor(() => expect(releaseStale).toBeDefined());
    fireEvent.click(refresh);
    await screen.findByText("Newest snapshot");
    releaseStale?.();
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(screen.getByText("Newest snapshot")).toBeTruthy();
    expect(screen.queryByText("Stale snapshot")).toBeNull();
  });

  it("reloads the board regularly only while a worker is online", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const boardLoads = (fetchMock: ReturnType<typeof boardApi>) =>
        fetchMock.mock.calls.filter(([input]) => String(input) === "/api/board")
          .length;

      const online = await renderBoard({
        workers: [worker({ status: "idle" })],
        columns: inProgress(),
      });
      const before = boardLoads(online);
      await vi.advanceTimersByTimeAsync(31_000);
      await waitFor(() => expect(boardLoads(online)).toBeGreaterThan(before));
    } finally {
      vi.useRealTimers();
    }
  });

  it("leaves the board alone while every worker is offline", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const fetchMock = await renderBoard({
        workers: [worker({ status: "offline" })],
        columns: inProgress(),
      });
      const boardLoads = () =>
        fetchMock.mock.calls.filter(([input]) => String(input) === "/api/board")
          .length;
      const before = boardLoads();
      await vi.advanceTimersByTimeAsync(31_000);
      expect(boardLoads()).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });
});
