// @vitest-environment jsdom

import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BoardCard, BoardColumns, Idea } from "../src/shared/types.js";

const timestamp = "2026-08-14T12:00:00.000Z";
const archivedAt = "2026-08-20T09:30:00.000Z";
const project = {
  id: "project-1",
  key: "APP",
  title: "Current Application",
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
  column: "done",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

/**
 * An in-memory board API: two tasks with done cards, `shipped` already
 * archived. Archive and unarchive requests move cards between the lists.
 */
const boardApi = () => {
  const ideas = [
    idea({ id: "idea-1", taskNumber: 1, title: "Finished login" }),
    idea({
      id: "idea-2",
      taskNumber: 2,
      title: "Shipped export",
      labels: ["export"],
    }),
  ];
  let active: BoardCard[] = [
    card({ id: "card-1", ideaId: "idea-1", title: "Finished login" }),
  ];
  let archived: BoardCard[] = [
    card({
      id: "card-2",
      ideaId: "idea-2",
      title: "Shipped export",
      labels: ["export"],
      branchName: "vp-export",
      archivedAt,
    }),
  ];
  const columns = (): BoardColumns => ({
    ready: [],
    planned: [],
    in_progress: [],
    review: [],
    done: active,
  });

  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path === "/api/auth/me") {
        return jsonResponse({ authenticated: true, username: "admin" });
      }
      if (path === "/api/projects") return jsonResponse({ items: [project] });
      if (path === "/api/ideas") return jsonResponse({ items: ideas });
      if (path === "/api/documents") return jsonResponse({ items: [] });
      if (path === "/api/github") return jsonResponse({ enabled: false });
      if (path === "/api/board") return jsonResponse({ columns: columns() });
      if (path === "/api/board/archived") {
        return jsonResponse({ items: archived });
      }
      if (path === "/api/board/archive-done" && method === "POST") {
        const moved = active.map((item) => ({ ...item, archivedAt }));
        archived = [...moved, ...archived];
        active = [];
        return jsonResponse({ items: moved });
      }
      const match = path.match(/^\/api\/board\/([^/]+)\/(archive|unarchive)$/);
      if (match && method === "POST") {
        const [, id, action] = match;
        if (action === "archive") {
          const target = active.find((item) => item.id === id) as BoardCard;
          active = active.filter((item) => item.id !== id);
          archived = [{ ...target, archivedAt }, ...archived];
          return jsonResponse({ item: archived[0] });
        }
        const target = archived.find((item) => item.id === id) as BoardCard;
        archived = archived.filter((item) => item.id !== id);
        const { archivedAt: _, ...restored } = target;
        active = [restored, ...active];
        return jsonResponse({ item: restored });
      }
      return jsonResponse({ error: "Not found" }, 404);
    },
  );
  return fetchMock;
};

const posts = (fetchMock: ReturnType<typeof boardApi>) =>
  fetchMock.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "POST")
    .map(([input]) => String(input));

const renderApp = async (path: string) => {
  window.history.replaceState(null, "", path);
  const fetchMock = boardApi();
  vi.stubGlobal("fetch", fetchMock);
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  return fetchMock;
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
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("archiving from the board", () => {
  it("archives a single done card", async () => {
    const fetchMock = await renderApp(`/projects/${project.id}/board`);

    await userEvent.click(
      await screen.findByRole("button", { name: "Archive Finished login" }),
    );

    expect(await screen.findByText("Archived Finished login.")).toBeTruthy();
    expect(screen.queryByText("Finished login")).toBeNull();
    expect(posts(fetchMock)).toEqual(["/api/board/card-1/archive"]);
  });

  it("confirms before archiving the whole done column", async () => {
    const fetchMock = await renderApp(`/projects/${project.id}/board`);

    await userEvent.click(
      await screen.findByRole("button", { name: "Archive all done" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Archive Done Cards",
    });
    expect(within(dialog).getByText("1 done card")).toBeTruthy();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cancel" }),
    );
    expect(posts(fetchMock)).toEqual([]);

    await userEvent.click(
      screen.getByRole("button", { name: "Archive all done" }),
    );
    await userEvent.click(
      within(
        await screen.findByRole("dialog", { name: "Archive Done Cards" }),
      ).getByRole("button", { name: "Archive Cards" }),
    );

    expect(await screen.findByText("Archived 1 done card.")).toBeTruthy();
    expect(screen.queryByText("Finished login")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Archive all done" }),
    ).toBeNull();
    const [, init] = fetchMock.mock.calls.find(
      ([input]) => String(input) === "/api/board/archive-done",
    ) as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ projectId: project.id });
  });
});

describe("the archive view", () => {
  it("opens from the sidebar below Board and lists archived tasks", async () => {
    await renderApp(`/projects/${project.id}/board`);

    const nav = await screen.findByRole("navigation");
    const links = within(nav)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(links).toEqual(["Tasks", "Board", "Archive", "Notes"]);

    await userEvent.click(within(nav).getByRole("link", { name: "Archive" }));

    expect(window.location.pathname).toBe(`/projects/${project.id}/archive`);
    expect(await screen.findByText("Shipped export")).toBeTruthy();
    expect(screen.getByText("APP-2")).toBeTruthy();
    expect(screen.getByText("vp-export")).toBeTruthy();
    expect(screen.getByText("export")).toBeTruthy();
    expect(screen.getByText(/^Archived /)).toBeTruthy();
  });

  it("searches archived tasks and restores one to Done", async () => {
    const fetchMock = await renderApp(`/projects/${project.id}/archive`);

    const search = await screen.findByLabelText("Search");
    await userEvent.type(search, "nothing like it");
    expect(
      await screen.findByText("No archived tasks match the search."),
    ).toBeTruthy();
    await userEvent.clear(search);
    await userEvent.type(search, "app-2");
    await userEvent.click(
      await screen.findByRole("button", { name: "Unarchive APP-2" }),
    );

    expect(
      await screen.findByText("Restored Shipped export to Done."),
    ).toBeTruthy();
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Unarchive APP-2" }),
      ).toBeNull(),
    );
    expect(posts(fetchMock)).toEqual(["/api/board/card-2/unarchive"]);
  });

  it("opens an archived task read-only with an Unarchive action", async () => {
    const fetchMock = await renderApp(`/projects/${project.id}/archive`);

    await userEvent.click(
      await screen.findByRole("button", { name: "Open APP-2" }),
    );
    const view = await screen.findByRole("dialog", { name: "Task Overview" });
    expect(within(view).getByText(/^Archived /)).toBeTruthy();
    expect(
      within(view).queryByRole("button", { name: "Edit Task" }),
    ).toBeNull();
    expect(within(view).queryByRole("button", { name: "Delete" })).toBeNull();

    await userEvent.click(
      within(view).getByRole("button", { name: "Unarchive" }),
    );
    expect(
      await screen.findByText("Restored Shipped export to Done."),
    ).toBeTruthy();
    expect(posts(fetchMock)).toEqual(["/api/board/card-2/unarchive"]);
  });

  it("marks archived tasks in the task list and locks their Ready toggle", async () => {
    await renderApp(`/projects/${project.id}/tasks`);

    const archivedToggle = await screen.findByRole("checkbox", {
      name: "Archived",
    });
    expect((archivedToggle as HTMLInputElement).checked).toBe(true);
    expect((archivedToggle as HTMLInputElement).disabled).toBe(true);
    expect(
      (screen.getByRole("checkbox", { name: "Ready" }) as HTMLInputElement)
        .checked,
    ).toBe(true);
  });
});
