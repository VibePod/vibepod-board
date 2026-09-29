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
  TaskEvent,
} from "../src/shared/types.js";

const timestamp = "2026-09-29T08:00:00.000Z";
const question = "Should the cache live in Redis or in Postgres?";

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
    id: "event-3",
    ideaId: "idea-2",
    kind: "feedback",
    actor: "admin",
    message: "Invalidate the cache on delete.",
    createdAt: "2026-09-29T10:00:00.000Z",
  },
  {
    id: "event-2",
    ideaId: "idea-2",
    kind: "answer",
    actor: "admin",
    message: "Postgres.",
    createdAt: "2026-09-29T09:00:00.000Z",
  },
  {
    id: "event-1",
    ideaId: "idea-2",
    kind: "question",
    actor: "claude@laptop",
    message: question,
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
    idea({ id: "idea-1", taskNumber: 1, title: "Add a cache" }),
    idea({ id: "idea-2", taskNumber: 2, title: "Reviewed cache" }),
  ];
  let asking = card({
    id: "card-1",
    ideaId: "idea-1",
    title: "Add a cache",
    blockedAt: timestamp,
    blockedReason: `Needs input: ${question}`,
    question,
  });
  let reviewed = card({
    id: "card-2",
    ideaId: "idea-2",
    title: "Reviewed cache",
    column: "review",
    branchName: "issue-7",
  });
  const columns = (): BoardColumns => ({
    ready: [],
    planned: [asking, ...(reviewed.column === "planned" ? [reviewed] : [])],
    in_progress: [],
    review: reviewed.column === "review" ? [reviewed] : [],
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
    if (path.endsWith("/history") || path.endsWith("/runs")) {
      return jsonResponse({ items: [] });
    }
    if (path === "/api/board/card-1/answer" && method === "POST") {
      asking = {
        ...asking,
        blockedAt: undefined,
        blockedReason: undefined,
        question: undefined,
      };
      return jsonResponse({ item: asking });
    }
    if (path === "/api/board/card-2/rework" && method === "POST") {
      reviewed = { ...reviewed, column: "planned" };
      return jsonResponse({ item: reviewed });
    }
    return jsonResponse({ error: "Not found" }, 404);
  });
};

const posts = (fetchMock: ReturnType<typeof boardApi>) =>
  fetchMock.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "POST")
    .map(([input, init]) => [
      String(input),
      JSON.parse(String((init as RequestInit).body)),
    ]);

const renderBoard = async () => {
  window.history.replaceState(null, "", `/projects/${project.id}/board`);
  const fetchMock = boardApi();
  vi.stubGlobal("fetch", fetchMock);
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  await screen.findByText("Add a cache");
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

describe("the needs-input and rework loop", () => {
  it("shows the agent's question and answers it", async () => {
    const fetchMock = await renderBoard();

    const asking = cardFor("Add a cache");
    expect(within(asking).getByText("Needs input")).toBeTruthy();
    expect(within(asking).getByText(question)).toBeTruthy();
    expect(within(asking).queryByText("Blocked")).toBeNull();

    await userEvent.click(
      within(asking).getByRole("button", { name: "Answer Add a cache" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Answer: Add a cache",
    });
    await userEvent.type(
      within(dialog).getByLabelText(/Answer/),
      "Postgres, no new services.",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Answer and plan again" }),
    );

    expect(
      await screen.findByText("Answered Add a cache; it is planned again."),
    ).toBeTruthy();
    expect(posts(fetchMock)).toEqual([
      ["/api/board/card-1/answer", { answer: "Postgres, no new services." }],
    ]);
    await waitFor(() =>
      expect(
        within(cardFor("Add a cache")).queryByText("Needs input"),
      ).toBeNull(),
    );
  });

  it("sends a reviewed task back with feedback", async () => {
    const fetchMock = await renderBoard();

    await userEvent.click(
      screen.getByRole("button", {
        name: "Request changes to Reviewed cache",
      }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Request changes: Reviewed cache",
    });
    expect(within(dialog).getByText(/continues on issue-7/)).toBeTruthy();
    await userEvent.type(
      within(dialog).getByLabelText(/Feedback/),
      "Invalidate the cache on delete.",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Send back to Planned" }),
    );

    expect(
      await screen.findByText("Sent Reviewed cache back for rework."),
    ).toBeTruthy();
    expect(posts(fetchMock)).toEqual([
      [
        "/api/board/card-2/rework",
        { feedback: "Invalidate the cache on delete." },
      ],
    ]);
  });

  it("asks for feedback when a reviewed card is dragged back to Planned", async () => {
    const fetchMock = await renderBoard();
    const dataTransfer = {
      setData: vi.fn(),
      getData: vi.fn(),
      effectAllowed: "move",
      dropEffect: "move",
    };

    fireEvent.dragStart(cardFor("Reviewed cache"), { dataTransfer });
    const planned = screen
      .getByText("Planned", { selector: ".column-title" })
      .closest(".column") as HTMLElement;
    fireEvent.drop(planned, { dataTransfer });

    expect(
      await screen.findByRole("dialog", {
        name: "Request changes: Reviewed cache",
      }),
    ).toBeTruthy();
    const patches = fetchMock.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patches).toEqual([]);
  });

  it("keeps questions, answers and feedback in the task history", async () => {
    await renderBoard();

    await userEvent.click(screen.getByText("Reviewed cache"));
    const view = await screen.findByRole("dialog", { name: "Task Overview" });

    expect(await within(view).findByText(question)).toBeTruthy();
    expect(within(view).getByText("Postgres.")).toBeTruthy();
    expect(
      within(view).getByText("Invalidate the cache on delete."),
    ).toBeTruthy();
    expect(within(view).getByText("Review feedback")).toBeTruthy();
    expect(
      within(view).getByRole("button", { name: "Request changes" }),
    ).toBeTruthy();
  });
});
