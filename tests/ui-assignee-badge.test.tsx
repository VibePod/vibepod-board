// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const timestamp = "2026-09-09T12:00:00.000Z";
const holder = "Claude::Subagent101::Worktree12";

const project = {
  id: "project-1",
  key: "APP",
  title: "Current Application",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};

const heldIdea = {
  id: "idea-1",
  projectId: "project-1",
  taskNumber: 1,
  title: "Held task",
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

const heldCard = {
  id: "card-1",
  projectId: "project-1",
  ideaId: "idea-1",
  title: "Held task",
  details: "",
  column: "ready",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  assignee: holder,
  createdAt: timestamp,
  updatedAt: timestamp,
};

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const stubFetch = (cards: unknown[]) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/auth/me") {
        return jsonResponse({ authenticated: true, username: "admin" });
      }
      if (path === "/api/projects") {
        return jsonResponse({ items: [project] });
      }
      if (path === "/api/ideas") {
        return jsonResponse({ items: [heldIdea] });
      }
      if (path === "/api/documents") {
        return jsonResponse({ items: [] });
      }
      if (path === "/api/board") {
        return jsonResponse({
          columns: {
            ready: cards,
            planned: [],
            in_progress: [],
            review: [],
            done: [],
          },
        });
      }
      return jsonResponse({ error: "Not found" }, 404);
    }),
  );
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
  document.body.innerHTML = "";
});

const loadAppShell = async () => {
  const module = await import("../src/client/main.js");
  return module.AppShell;
};

describe("assignee badge", () => {
  it("names the holder on the task list card", async () => {
    window.history.replaceState(null, "", "/projects/project-1/tasks");
    stubFetch([heldCard]);
    const AppShell = await loadAppShell();

    render(<AppShell />);

    expect(await screen.findByText(holder)).toBeDefined();
  });

  it("names the holder on the board card without opening it", async () => {
    window.history.replaceState(null, "", "/projects/project-1/board");
    stubFetch([heldCard]);
    const AppShell = await loadAppShell();

    render(<AppShell />);

    expect(await screen.findByText(holder)).toBeDefined();
  });

  it("shows no holder badge on unclaimed work", async () => {
    window.history.replaceState(null, "", "/projects/project-1/board");
    stubFetch([{ ...heldCard, assignee: undefined }]);
    const AppShell = await loadAppShell();

    render(<AppShell />);

    expect(await screen.findByText("Held task")).toBeDefined();
    expect(screen.queryByText(holder)).toBeNull();
  });
});
