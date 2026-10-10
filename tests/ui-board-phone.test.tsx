// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { boardColumnStorageKey } from "../src/client/boardColumnPicker.js";
import type { BoardCard, BoardColumns, Idea } from "../src/shared/types.js";

const timestamp = "2026-08-14T12:00:00.000Z";
const project = {
  id: "project-1",
  key: "VP",
  title: "Current Application",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};

const idea = (
  patch: Partial<Idea> & Pick<Idea, "id" | "taskNumber">,
): Idea => ({
  projectId: project.id,
  title: "",
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

const card = (
  patch: Partial<BoardCard> & Pick<BoardCard, "id" | "title" | "column">,
): BoardCard => ({
  projectId: project.id,
  details: "",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  updatedAt: timestamp,
  ...patch,
});

const ideas = [
  idea({ id: "idea-1", taskNumber: 1, title: "Plan the cache" }),
  idea({ id: "idea-2", taskNumber: 2, title: "Write the docs" }),
  idea({ id: "idea-3", taskNumber: 3, title: "Ship the cache" }),
  idea({ id: "idea-4", taskNumber: 4, title: "Review the cache" }),
];

let columns: BoardColumns;
let phone: boolean;

const initialColumns = (): BoardColumns => ({
  ready: [],
  planned: [
    card({
      id: "c-1",
      ideaId: "idea-1",
      title: "Plan the cache",
      column: "planned",
    }),
    card({
      id: "c-2",
      ideaId: "idea-2",
      title: "Write the docs",
      labels: ["docs"],
      column: "planned",
    }),
  ],
  in_progress: [
    card({
      id: "c-3",
      ideaId: "idea-3",
      title: "Ship the cache",
      column: "in_progress",
    }),
  ],
  review: [
    card({
      id: "c-4",
      ideaId: "idea-4",
      title: "Review the cache",
      column: "review",
    }),
  ],
  pr_ready: [],
  done: [],
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const fetchMock = vi.fn(
  async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/auth/me") {
      return jsonResponse({ authenticated: true, username: "admin" });
    }
    if (path === "/api/projects") return jsonResponse({ items: [project] });
    if (path === "/api/ideas") return jsonResponse({ items: ideas });
    if (path === "/api/documents") return jsonResponse({ items: [] });
    if (path === "/api/github") return jsonResponse({ enabled: false });
    if (path === "/api/board") return jsonResponse({ columns });
    const cardPath = /^\/api\/board\/([^/]+)$/.exec(path);
    if (cardPath && init?.method === "PATCH") {
      const target = JSON.parse(String(init.body)).column as BoardColumn;
      const moved = Object.values(columns)
        .flat()
        .find((item) => item.id === cardPath[1]) as BoardCard;
      for (const column of Object.keys(columns) as BoardColumn[]) {
        columns[column] = columns[column].filter(
          (item) => item.id !== moved.id,
        );
      }
      columns[target] = [...columns[target], { ...moved, column: target }];
      return jsonResponse({ item: { ...moved, column: target } });
    }
    return jsonResponse({ error: "Not found" }, 404);
  },
);

type BoardColumn = keyof BoardColumns;

beforeEach(() => {
  columns = initialColumns();
  phone = true;
  window.localStorage.clear();
  fetchMock.mockClear();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: phone && query.includes("max-width"),
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
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetModules();
});

const renderBoard = async (firstCard = "Ship the cache") => {
  window.history.replaceState(null, "", `/projects/${project.id}/board`);
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  await screen.findByText(firstCard);
};

const picker = () =>
  screen.getByRole("combobox", { name: "Column" }) as HTMLSelectElement;

const pickerOptions = () =>
  Array.from(picker().options).map((option) => option.textContent);

const columnTitles = () =>
  Array.from(document.querySelectorAll(".column-title")).map(
    (element) => element.textContent,
  );

const visibleCards = () =>
  screen
    .queryAllByRole("button")
    .filter((element) => element.classList.contains("compact-card"))
    .map((element) => within(element).getByRole("heading").textContent);

const cardFor = (title: string) =>
  screen.getByText(title).closest(".compact-card") as HTMLElement;

describe("board on a phone", () => {
  it("shows one column, picked from a dropdown with every column's count", async () => {
    await renderBoard();

    // First visit: the work in progress.
    expect(picker().value).toBe("in_progress");
    expect(pickerOptions()).toEqual([
      "Ready (0)",
      "Planned (2)",
      "In Progress (1)",
      "Review (1)",
      "PR ready (0)",
      "Done (0)",
    ]);
    expect(columnTitles()).toEqual(["In Progress"]);
    expect(visibleCards()).toEqual(["Ship the cache"]);

    fireEvent.change(picker(), { target: { value: "planned" } });
    expect(columnTitles()).toEqual(["Planned"]);
    expect(visibleCards()).toEqual(["Plan the cache", "Write the docs"]);
    expect(cardFor("Plan the cache").getAttribute("draggable")).toBe("false");
  });

  it("opens Review on a first visit when nothing is in progress", async () => {
    columns = { ...initialColumns(), in_progress: [] };
    await renderBoard("Review the cache");

    expect(picker().value).toBe("review");
    expect(visibleCards()).toEqual(["Review the cache"]);
  });

  it("remembers the picked column per project", async () => {
    await renderBoard();
    fireEvent.change(picker(), { target: { value: "review" } });
    expect(window.localStorage.getItem(boardColumnStorageKey(project.id))).toBe(
      "review",
    );

    cleanup();
    vi.resetModules();
    await renderBoard("Review the cache");
    expect(picker().value).toBe("review");
    expect(visibleCards()).toEqual(["Review the cache"]);
  });

  it("applies the search to the shown column and the counts", async () => {
    await renderBoard();
    fireEvent.change(picker(), { target: { value: "planned" } });

    fireEvent.change(screen.getByRole("textbox", { name: "Search" }), {
      target: { value: "docs" },
    });

    await waitFor(() => expect(visibleCards()).toEqual(["Write the docs"]));
    expect(pickerOptions()).toEqual([
      "Ready (0)",
      "Planned (1)",
      "In Progress (0)",
      "Review (0)",
      "PR ready (0)",
      "Done (0)",
    ]);
  });

  it("moves a card with its Move to menu", async () => {
    await renderBoard();

    await userEvent.click(
      screen.getByRole("button", { name: "Move Ship the cache to…" }),
    );
    // jsdom has no layout, so Mantine hides the dropdown as detached from its target.
    const review = await screen.findByRole("menuitem", {
      name: "Review",
      hidden: true,
    });
    expect(
      screen
        .getAllByRole("menuitem", { hidden: true })
        .map((item) => item.textContent),
    ).toEqual(["Ready", "Planned", "Review", "PR ready", "Done"]);
    await userEvent.click(review);

    await waitFor(() => expect(visibleCards()).toEqual([]));
    const patches = fetchMock.mock.calls.filter(
      ([, init]) => init?.method === "PATCH",
    );
    expect(
      patches.map(([path, init]) => [path, JSON.parse(String(init?.body))]),
    ).toEqual([["/api/board/c-3", { column: "review" }]]);
    expect(screen.queryByRole("dialog", { name: "Task Overview" })).toBeNull();

    fireEvent.change(picker(), { target: { value: "review" } });
    expect(visibleCards()).toEqual(["Review the cache", "Ship the cache"]);
  });

  it("asks for feedback when a reviewed card is moved back to Planned", async () => {
    await renderBoard();
    fireEvent.change(picker(), { target: { value: "review" } });

    await userEvent.click(
      screen.getByRole("button", { name: "Move Review the cache to…" }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "Planned", hidden: true }),
    );

    expect(
      await screen.findByRole("dialog", {
        name: "Request changes: Review the cache",
      }),
    ).toBeTruthy();
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH"),
    ).toEqual([]);
  });
});

it("keeps every column and no picker on wider screens", async () => {
  phone = false;
  await renderBoard();

  expect(screen.queryByRole("combobox", { name: "Column" })).toBeNull();
  expect(columnTitles()).toEqual([
    "Ready",
    "Planned",
    "In Progress",
    "Review",
    "PR ready",
    "Done",
  ]);
  expect(
    screen.queryByRole("button", { name: "Move Ship the cache to…" }),
  ).toBeNull();
});
