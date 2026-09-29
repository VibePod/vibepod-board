// @vitest-environment jsdom

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  BoardCard,
  BoardColumns,
  Idea,
  TaskEvent,
} from "../src/shared/types.js";

const timestamp = "2026-09-29T08:00:00.000Z";
const holder = "Claude::Runner::laptop";

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
  column: "planned",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  attempts: 0,
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const history: TaskEvent[] = [
  {
    id: "event-2",
    ideaId: "idea-2",
    kind: "blocked",
    actor: holder,
    message: "Attempt 3 failed; blocked after 3 failed attempts: tests fail",
    createdAt: "2026-09-29T09:00:00.000Z",
  },
  {
    id: "event-1",
    ideaId: "idea-2",
    kind: "claimed",
    actor: holder,
    message: `Claimed by ${holder} (attempt 3)`,
    createdAt: "2026-09-29T08:30:00.000Z",
  },
];

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const boardApi = () => {
  const ideas = [
    idea({ id: "idea-1", taskNumber: 1, title: "Claimed work" }),
    idea({ id: "idea-2", taskNumber: 2, title: "Stuck work" }),
    idea({ id: "idea-3", taskNumber: 3, title: "Flaky work" }),
  ];
  const claimed = card({
    id: "card-1",
    ideaId: "idea-1",
    title: "Claimed work",
    column: "in_progress",
    assignee: holder,
    claimedAt: "2026-09-29T08:15:00.000Z",
    claimExpiresAt: "2026-09-29T08:30:00.000Z",
  });
  let blocked = card({
    id: "card-2",
    ideaId: "idea-2",
    title: "Stuck work",
    attempts: 3,
    blockedAt: "2026-09-29T09:00:00.000Z",
    blockedReason: "Failed 3 attempts: tests fail",
  });
  const flaky = card({
    id: "card-3",
    ideaId: "idea-3",
    title: "Flaky work",
    attempts: 1,
  });
  const columns = (): BoardColumns => ({
    ready: [],
    planned: [blocked, flaky],
    in_progress: [claimed],
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
    if (path === "/api/ideas") return jsonResponse({ items: ideas });
    if (path === "/api/documents") return jsonResponse({ items: [] });
    if (path === "/api/board") return jsonResponse({ columns: columns() });
    if (path === "/api/board/archived") return jsonResponse({ items: [] });
    if (path === "/api/ideas/idea-2/history") {
      return jsonResponse({ items: history });
    }
    if (path.endsWith("/history")) return jsonResponse({ items: [] });
    if (path === "/api/board/card-2" && method === "PATCH") {
      blocked = {
        ...blocked,
        attempts: 0,
        blockedAt: undefined,
        blockedReason: undefined,
      };
      return jsonResponse({ item: blocked });
    }
    return jsonResponse({ error: "Not found" }, 404);
  });
};

const renderBoard = async () => {
  window.history.replaceState(null, "", `/projects/${project.id}/board`);
  const fetchMock = boardApi();
  vi.stubGlobal("fetch", fetchMock);
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  await screen.findByText("Claimed work");
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

describe("claimed and blocked cards", () => {
  it("shows who claimed a task and since when", async () => {
    await renderBoard();

    const claimed = cardFor("Claimed work");
    expect(within(claimed).getByText(holder)).toBeTruthy();
    const since = claimed.querySelector(".board-card-claimed-since");
    expect(since?.textContent).toMatch(/^since /);
  });

  it("shows why a card is blocked and unblocks it by putting it in Planned", async () => {
    const fetchMock = await renderBoard();

    const blocked = cardFor("Stuck work");
    expect(within(blocked).getByText("Blocked")).toBeTruthy();
    expect(
      within(blocked).getByText("Failed 3 attempts: tests fail"),
    ).toBeTruthy();

    await userEvent.click(
      within(blocked).getByRole("button", { name: "Unblock Stuck work" }),
    );

    expect(await screen.findByText("Unblocked Stuck work.")).toBeTruthy();
    const patches = fetchMock.mock.calls
      .filter(
        ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
      )
      .map(([input, init]) => [
        String(input),
        JSON.parse(String((init as RequestInit).body)),
      ]);
    expect(patches).toEqual([["/api/board/card-2", { column: "planned" }]]);
    await waitFor(() =>
      expect(within(cardFor("Stuck work")).queryByText("Blocked")).toBeNull(),
    );
  });

  it("counts failed attempts on a card that is not blocked", async () => {
    await renderBoard();

    expect(
      within(cardFor("Flaky work")).getByText("1 failed attempt"),
    ).toBeTruthy();
    expect(
      within(cardFor("Claimed work")).queryByText(/failed attempt/),
    ).toBeNull();
  });

  it("lists the task history in the task view", async () => {
    await renderBoard();

    await userEvent.click(screen.getByText("Stuck work"));
    const view = await screen.findByRole("dialog", { name: "Task Overview" });

    expect(
      await within(view).findByText(
        "Attempt 3 failed; blocked after 3 failed attempts: tests fail",
      ),
    ).toBeTruthy();
    expect(
      within(view).getByText(`Claimed by ${holder} (attempt 3)`),
    ).toBeTruthy();
    expect(view.querySelector(".overview-blocked")).toBeTruthy();
  });
});
