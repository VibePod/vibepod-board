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

import type { Idea, Project } from "../src/shared/types.js";

const timestamp = "2026-08-14T12:00:00.000Z";
const archivedAt = "2026-10-20T09:30:00.000Z";

const project = (patch: Partial<Project>): Project => ({
  id: "project-1",
  key: "APP",
  title: "Current Application",
  summary: "",
  taskCount: 1,
  cardCount: 0,
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const legacyTask: Idea = {
  id: "idea-1",
  projectId: "project-2",
  taskNumber: 7,
  title: "Retire the old login",
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
};

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const emptyColumns = {
  ready: [],
  planned: [],
  in_progress: [],
  review: [],
  pr_ready: [],
  done: [],
};

/** An in-memory API with one active and one archived project. */
const projectApi = () => {
  let active = [project({})];
  let archived = [
    project({ id: "project-2", key: "OLD", title: "Legacy", archivedAt }),
  ];
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path === "/api/auth/me") {
      return jsonResponse({ authenticated: true, username: "admin" });
    }
    if (path === "/api/projects") return jsonResponse({ items: active });
    if (path === "/api/projects?archived=true") {
      return jsonResponse({ items: archived });
    }
    if (path === "/api/ideas?projectId=project-2") {
      return jsonResponse({ items: [legacyTask] });
    }
    if (path.startsWith("/api/ideas")) return jsonResponse({ items: [] });
    if (path.startsWith("/api/documents")) return jsonResponse({ items: [] });
    if (path === "/api/board/archived") return jsonResponse({ items: [] });
    if (path.startsWith("/api/board")) {
      return jsonResponse({ columns: emptyColumns });
    }
    if (path === "/api/github") return jsonResponse({ enabled: false });
    const archive = path.match(/^\/api\/projects\/([^/]+)\/(archive|restore)$/);
    if (archive && method === "POST") {
      const [, id, action] = archive;
      if (action === "archive") {
        const target = active.find((item) => item.id === id) as Project;
        active = active.filter((item) => item.id !== id);
        archived = [{ ...target, archivedAt }, ...archived];
        return jsonResponse({ item: archived[0] });
      }
      const target = archived.find((item) => item.id === id) as Project;
      archived = archived.filter((item) => item.id !== id);
      active = [{ ...target, archivedAt: null }, ...active];
      return jsonResponse({ item: active[0] });
    }
    const removed = path.match(/^\/api\/projects\/([^/?]+)\?confirmKey=(.+)$/);
    if (removed && method === "DELETE") {
      const target = archived.find((item) => item.id === removed[1]);
      archived = archived.filter((item) => item.id !== removed[1]);
      return jsonResponse(target);
    }
    return jsonResponse({ error: `Unexpected ${method} ${path}` }, 404);
  });
};

const renderApp = async (path: string) => {
  window.history.replaceState(null, "", path);
  const fetchMock = projectApi();
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

describe("archived projects", () => {
  it("archives a project from its card after a confirmation", async () => {
    const fetchMock = await renderApp("/projects");

    await userEvent.click(
      await screen.findByRole("button", { name: "Archive" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Archive Project",
    });
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Archive Project" }),
    );

    expect(
      await screen.findByText(
        "Project APP archived. It is listed under Archived projects.",
      ),
    ).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/projects/project-1/archive",
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.getByText("0 projects")).toBeTruthy();
  });

  it("lists archived projects from the top-level entry and opens one read-only", async () => {
    await renderApp("/projects");

    const topnav = await screen.findByRole("navigation", { name: "Projects" });
    await userEvent.click(
      within(topnav).getByRole("link", { name: "Archived projects" }),
    );

    expect(window.location.pathname).toBe("/archived-projects");
    expect(await screen.findByText("Legacy")).toBeTruthy();
    expect(screen.getByText("1 task, 0 cards")).toBeTruthy();
    // Archived projects are not offered by the project switcher.
    expect(screen.queryByText("OLD - Legacy")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Open" }));

    expect(window.location.pathname).toBe("/archived-projects/project-2");
    expect(await screen.findByText("Retire the old login")).toBeTruthy();
    expect(screen.getByText("OLD-7")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add Task" })).toBeNull();
  });

  it("deletes an archived project only after its key is typed", async () => {
    const fetchMock = await renderApp("/archived-projects");

    await userEvent.click(
      await screen.findByRole("button", { name: "Delete permanently" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Delete Project Permanently",
    });
    const confirm = within(dialog).getByRole("button", {
      name: "Delete permanently",
    });
    expect(confirm.hasAttribute("disabled")).toBe(true);
    expect(
      within(dialog).getByRole("button", { name: "Export first" }),
    ).toBeTruthy();

    await userEvent.type(
      within(dialog).getByLabelText("Type OLD to confirm"),
      "old",
    );
    await userEvent.click(confirm);

    expect(
      await screen.findByText("Project OLD deleted permanently."),
    ).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/projects/project-2?confirmKey=OLD",
      expect.objectContaining({ method: "DELETE" }),
    );
    await waitFor(() =>
      expect(screen.getByText("No archived projects")).toBeTruthy(),
    );
  });

  it("restores an archived project into the project list", async () => {
    await renderApp("/archived-projects");

    await userEvent.click(
      await screen.findByRole("button", { name: "Restore" }),
    );

    expect(
      await screen.findByText(
        "Project OLD restored. Its automation stays paused until resumed.",
      ),
    ).toBeTruthy();
    expect(window.location.pathname).toBe("/projects/project-2/tasks");
  });
});
