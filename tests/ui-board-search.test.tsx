// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BoardCard, Idea } from "../src/shared/types.js";

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
  ...patch,
});

const ideas = [
  idea({ id: "idea-85", taskNumber: 85, title: "Target task" }),
  idea({ id: "idea-850", taskNumber: 850, title: "Longer number task" }),
  idea({ id: "idea-3", taskNumber: 3, title: "Raise limit to 85 requests" }),
];

const columns = {
  ready: [
    card({
      id: "c-85",
      ideaId: "idea-85",
      title: "Target task",
      column: "ready",
    }),
  ],
  planned: [
    card({
      id: "c-850",
      ideaId: "idea-850",
      title: "Longer number task",
      column: "planned",
    }),
    card({
      id: "c-loose",
      title: "Loose card",
      labels: ["ops"],
      column: "planned",
    }),
  ],
  in_progress: [
    card({
      id: "c-3",
      ideaId: "idea-3",
      title: "Raise limit to 85 requests",
      column: "in_progress",
    }),
  ],
  review: [],
  pr_ready: [],
  done: [],
};

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

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
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/auth/me") {
        return jsonResponse({ authenticated: true, username: "admin" });
      }
      if (path === "/api/projects") return jsonResponse({ items: [project] });
      if (path === "/api/ideas") return jsonResponse({ items: ideas });
      if (path === "/api/documents") return jsonResponse({ items: [] });
      if (path === "/api/github") return jsonResponse({ enabled: false });
      if (path === "/api/board") return jsonResponse({ columns });
      return jsonResponse({ error: "Not found" }, 404);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

const renderBoard = async (url: string) => {
  window.history.replaceState(null, "", url);
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  await screen.findByText("Target task");
};

const visibleCards = () =>
  screen
    .queryAllByRole("button")
    .filter((element) => element.classList.contains("compact-card"))
    .map((element) => within(element).getByRole("heading").textContent);

const columnCount = (label: string) =>
  screen.getByRole("heading", { name: label }).parentElement?.textContent;

describe("board search", () => {
  it("filters every column with the task search and keeps it in the URL", async () => {
    await renderBoard(`/projects/${project.id}/board`);
    const search = screen.getByRole("textbox", { name: "Search" });
    expect(search.getAttribute("placeholder")).toBe(
      "Search ID, title, details, labels",
    );
    expect(visibleCards()).toHaveLength(4);
    expect(columnCount("Planned")).toBe("Planned2");

    fireEvent.change(search, { target: { value: "vp-85" } });
    await waitFor(() => expect(visibleCards()).toEqual(["Target task"]));
    expect(columnCount("Ready")).toBe("Ready1 / 1");
    expect(columnCount("Planned")).toBe("Planned0 / 2");
    expect(screen.getByText("1 of 4 cards")).toBeTruthy();
    expect(window.location.search).toBe("?q=vp-85");

    fireEvent.change(search, { target: { value: "#85" } });
    await waitFor(() => expect(visibleCards()).toEqual(["Target task"]));

    fireEvent.change(search, { target: { value: "OPS" } });
    await waitFor(() => expect(visibleCards()).toEqual(["Loose card"]));

    fireEvent.change(search, { target: { value: "number" } });
    await waitFor(() => expect(visibleCards()).toEqual(["Longer number task"]));

    fireEvent.change(search, { target: { value: "" } });
    await waitFor(() => expect(visibleCards()).toHaveLength(4));
    expect(window.location.search).toBe("");
    expect(screen.getByText("4 cards")).toBeTruthy();
  });

  it("applies the search from a shared board link", async () => {
    await renderBoard(`/projects/${project.id}/board?q=85`);

    expect(
      (screen.getByRole("textbox", { name: "Search" }) as HTMLInputElement)
        .value,
    ).toBe("85");
    expect(visibleCards()).toEqual(["Target task"]);
    expect(window.location.search).toBe("?q=85");
  });
});
