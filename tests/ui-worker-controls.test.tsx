// @vitest-environment jsdom

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AutomationState,
  BoardCard,
  BoardColumns,
  Idea,
  TaskRun,
  Worker,
} from "../src/shared/types.js";

const timestamp = "2026-09-29T08:00:00.000Z";
const holder = "claude@laptop";

const project = {
  id: "project-1",
  key: "VP",
  title: "VibePod",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};

const idea: Idea = {
  id: "idea-1",
  projectId: project.id,
  taskNumber: 1,
  title: "Automated login",
  summary: "",
  details: "",
  status: "ready",
  labels: [],
  acceptanceCriteria: [],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  assignee: holder,
  createdAt: timestamp,
  updatedAt: timestamp,
};

const claimedCard: BoardCard = {
  id: "card-1",
  projectId: project.id,
  ideaId: "idea-1",
  title: "Automated login",
  details: "",
  column: "in_progress",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  assignee: holder,
  claimedAt: "2026-09-29T08:10:00.000Z",
  claimExpiresAt: "2026-09-29T08:25:00.000Z",
  attempts: 0,
  createdAt: timestamp,
  updatedAt: timestamp,
};

const worker: Worker = {
  id: "worker-1",
  projectId: project.id,
  name: holder,
  agent: "claude",
  machine: "laptop",
  status: "working",
  taskId: "idea-1",
  taskKey: "VP-1",
  taskTitle: "Automated login",
  step: "agent_running",
  taskStartedAt: "2026-09-29T08:10:00.000Z",
  startedAt: timestamp,
  lastSeenAt: new Date().toISOString(),
};

const runs: TaskRun[] = [
  {
    id: "run-2",
    ideaId: "idea-1",
    workerName: holder,
    agent: "claude",
    outcome: "failed",
    summary: "Tried to **rewrite** the session store.",
    commits: [{ sha: "a1b2c3d4e5f6", subject: "Rewrite session store" }],
    branchName: "vp-1",
    verifyCommand: "npm test",
    verifyExitCode: 1,
    verifyOutput: "FAIL tests/login.test.ts",
    verifyOutputTruncated: true,
    durationSeconds: 725,
    failureReason: "Verify command exited with 1",
    startedAt: "2026-09-29T07:00:00.000Z",
    createdAt: "2026-09-29T07:12:05.000Z",
  },
  {
    id: "run-1",
    ideaId: "idea-1",
    workerName: holder,
    outcome: "usage_limit",
    summary: "",
    commits: [],
    verifyOutputTruncated: false,
    createdAt: "2026-09-29T06:00:00.000Z",
  },
];

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const boardApi = () => {
  let automation: AutomationState = { projectId: project.id, paused: false };
  let card = claimedCard;
  const columns = (): BoardColumns => ({
    ready: [],
    planned: card.column === "planned" ? [card] : [],
    in_progress: card.column === "in_progress" ? [card] : [],
    review: [],
    done: [],
  });
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path === "/api/auth/me") {
      return jsonResponse({ authenticated: true, username: "admin" });
    }
    if (path === "/api/projects") return jsonResponse({ items: [project] });
    if (path === "/api/ideas") return jsonResponse({ items: [idea] });
    if (path === "/api/documents") return jsonResponse({ items: [] });
    if (path === "/api/board") return jsonResponse({ columns: columns() });
    if (path === "/api/board/archived") return jsonResponse({ items: [] });
    if (path === `/api/workers?projectId=${project.id}`) {
      return jsonResponse({ items: [worker], automation });
    }
    if (path === "/api/projects/project-1/automation/pause") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      automation = {
        projectId: project.id,
        paused: true,
        pausedAt: timestamp,
        reason: body.reason,
      };
      return jsonResponse(automation);
    }
    if (path === "/api/projects/project-1/automation/resume") {
      automation = { projectId: project.id, paused: false };
      return jsonResponse(automation);
    }
    if (path === "/api/workers/worker-1/stop" && method === "POST") {
      return jsonResponse({ item: { ...worker, stopRequestedAt: timestamp } });
    }
    if (path === "/api/board/card-1/cancel" && method === "POST") {
      card = {
        ...card,
        column: "planned",
        assignee: undefined,
        claimedAt: undefined,
        claimExpiresAt: undefined,
      };
      return jsonResponse({ item: card });
    }
    if (path === "/api/ideas/idea-1/runs") return jsonResponse({ items: runs });
    if (path === "/api/ideas/idea-1/history")
      return jsonResponse({ items: [] });
    return jsonResponse({ error: "Not found" }, 404);
  });
};

const posts = (fetchMock: ReturnType<typeof boardApi>) =>
  fetchMock.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "POST")
    .map(([input, init]) => [
      String(input),
      (init as RequestInit).body
        ? JSON.parse(String((init as RequestInit).body))
        : null,
    ]);

const renderBoard = async () => {
  window.history.replaceState(null, "", `/projects/${project.id}/board`);
  const fetchMock = boardApi();
  vi.stubGlobal("fetch", fetchMock);
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  await screen.findByText("Automated login");
  return fetchMock;
};

const openWorkers = async () => {
  await userEvent.click(
    await screen.findByRole("button", { name: /1 worker/ }),
  );
  return screen.findByRole("dialog", { name: "Workers" });
};

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

describe("worker controls", () => {
  it("pauses and resumes automation of the project", async () => {
    const fetchMock = await renderBoard();
    const dialog = await openWorkers();

    await userEvent.type(
      within(dialog).getByLabelText("Pause reason"),
      "Release freeze",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Pause automation" }),
    );

    expect(await within(dialog).findByText("Automation paused")).toBeTruthy();
    expect(within(dialog).getByText("Release freeze")).toBeTruthy();
    expect(
      await screen.findByRole("button", {
        name: /^Automation paused · 1 worker/,
      }),
    ).toBeTruthy();

    await userEvent.click(
      within(dialog).getByRole("button", { name: "Resume automation" }),
    );
    expect(
      await within(dialog).findByRole("button", { name: "Pause automation" }),
    ).toBeTruthy();
    expect(posts(fetchMock)).toEqual([
      [
        "/api/projects/project-1/automation/pause",
        { reason: "Release freeze" },
      ],
      ["/api/projects/project-1/automation/resume", null],
    ]);
  });

  it("stops a worker from the list", async () => {
    const fetchMock = await renderBoard();
    const dialog = await openWorkers();

    await userEvent.click(
      within(dialog).getByRole("button", { name: `Stop ${holder}` }),
    );

    expect(await screen.findByText(`Asked ${holder} to stop.`)).toBeTruthy();
    expect(posts(fetchMock)).toEqual([["/api/workers/worker-1/stop", null]]);
  });

  it("cancels a run from the card after confirming", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = await renderBoard();

    await userEvent.click(
      screen.getByRole("button", { name: "Cancel run of Automated login" }),
    );

    expect(confirm).toHaveBeenCalledOnce();
    expect(
      await screen.findByText("Cancelled the run of Automated login."),
    ).toBeTruthy();
    expect(posts(fetchMock)).toEqual([
      [
        "/api/board/card-1/cancel",
        { expectedUpdatedAt: "2026-09-29T08:00:00.000Z" },
      ],
    ]);
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Cancel run of Automated login" }),
      ).toBeNull(),
    );
  });

  it("keeps the run when the cancel is not confirmed", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchMock = await renderBoard();

    await userEvent.click(
      screen.getByRole("button", { name: "Cancel run of Automated login" }),
    );

    expect(posts(fetchMock)).toEqual([]);
  });

  it("lists the run reports of a task as its history of attempts", async () => {
    await renderBoard();

    await userEvent.click(screen.getByText("Automated login"));
    const view = await screen.findByRole("dialog", { name: "Task Overview" });
    const section = await waitFor(() => {
      const found = view.querySelector(".task-runs");
      if (!found) throw new Error("no run reports yet");
      return found as HTMLElement;
    });

    const reports = section.querySelectorAll(".task-run");
    expect(reports).toHaveLength(2);
    const [latest, earlier] = Array.from(reports) as HTMLElement[];
    expect(within(latest).getByText("Failed")).toBeTruthy();
    expect(
      within(latest).getByText("Verify command exited with 1"),
    ).toBeTruthy();
    expect(within(latest).getByText("rewrite")).toBeTruthy();
    expect(within(latest).getByText("a1b2c3d4")).toBeTruthy();
    expect(within(latest).getByText("Rewrite session store")).toBeTruthy();
    expect(within(latest).getByText("npm test")).toBeTruthy();
    expect(within(latest).getByText("exit 1")).toBeTruthy();
    expect(within(latest).getByText("FAIL tests/login.test.ts")).toBeTruthy();
    expect(within(latest).getByText(/12 min 5 s/)).toBeTruthy();
    expect(within(earlier).getByText("Usage limit")).toBeTruthy();
    expect(
      within(view).getByRole("button", { name: "Cancel run" }),
    ).toBeTruthy();
  });

  it("reloads the run reports with the board and while open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const fetchMock = await renderBoard();
      const runLoads = () =>
        fetchMock.mock.calls.filter(
          ([input]) => String(input) === "/api/ideas/idea-1/runs",
        ).length;

      await userEvent.click(screen.getByText("Automated login"));
      await screen.findByRole("dialog", { name: "Task Overview" });
      await waitFor(() => expect(runLoads()).toBeGreaterThan(0));
      const opened = runLoads();

      await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
      await waitFor(() => expect(runLoads()).toBeGreaterThan(opened));
      const refreshed = runLoads();

      await vi.advanceTimersByTimeAsync(15_000);
      await waitFor(() => expect(runLoads()).toBeGreaterThan(refreshed));
    } finally {
      vi.useRealTimers();
    }
  });
});
