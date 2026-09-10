// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const timestamp = "2026-09-10T09:00:00.000Z";
const ada = "Claude::Subagent101::Worktree12";
const grace = "Claude::Subagent202::Worktree7";

const project = {
  id: "project-1",
  key: "APP",
  title: "Current Application",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};

const idea = (id: string, title: string, assignee?: string) => ({
  id,
  projectId: "project-1",
  taskNumber: Number(id.replace(/\D/g, "")),
  title,
  summary: "",
  details: "",
  status: "ready",
  labels: [],
  acceptanceCriteria: [],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  assignee,
  createdAt: timestamp,
  updatedAt: timestamp,
});

const card = (
  id: string,
  ideaId: string,
  title: string,
  assignee?: string,
) => ({
  id,
  projectId: "project-1",
  ideaId,
  title,
  details: "",
  column: "ready",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  assignee,
  createdAt: timestamp,
  updatedAt: timestamp,
});

const ideas = [
  idea("idea-1", "Mine", ada),
  idea("idea-2", "Theirs", grace),
  idea("idea-3", "Free"),
];

const cards = [
  card("card-1", "idea-1", "Mine", ada),
  card("card-2", "idea-2", "Theirs", grace),
  card("card-3", "idea-3", "Free"),
];

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const stubFetch = () => {
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
        return jsonResponse({ items: ideas });
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

/**
 * Mantine keeps the dropdown mounted but hidden, and jsdom never opens it, so
 * the options are read and clicked where they live: in the listbox the filter's
 * own label names. Clicking one still runs Mantine's own selection handler.
 */
const assigneeOptions = async () => {
  const input = await screen.findByRole("textbox", { name: "Assignee" });
  const listbox = document.querySelector(
    `[aria-labelledby="${input.id}-label"]`,
  );
  if (!listbox) {
    throw new Error("assignee filter has no option list");
  }
  return Array.from(listbox.querySelectorAll('[role="option"]'));
};

const pickAssignee = async (label: string) => {
  const option = (await assigneeOptions()).find(
    (entry) => entry.textContent === label,
  );
  if (!option) {
    throw new Error(`assignee filter does not offer ${label}`);
  }
  fireEvent.click(option);
};

describe("assignee filter", () => {
  it("keeps one holder's cards on the board and hides the rest", async () => {
    window.history.replaceState(null, "", "/projects/project-1/board");
    stubFetch();
    const AppShell = await loadAppShell();

    render(<AppShell />);
    expect(await screen.findByText("Theirs")).toBeDefined();

    await pickAssignee(ada);

    await waitFor(() => {
      expect(screen.queryByText("Theirs")).toBeNull();
    });
    expect(screen.getByText("Mine")).toBeDefined();
    expect(screen.queryByText("Free")).toBeNull();
    expect(screen.getByText("1 of 3 cards")).toBeDefined();
  });

  it("keeps only unclaimed cards under Unassigned", async () => {
    window.history.replaceState(null, "", "/projects/project-1/board");
    stubFetch();
    const AppShell = await loadAppShell();

    render(<AppShell />);
    expect(await screen.findByText("Mine")).toBeDefined();

    await pickAssignee("Unassigned");

    await waitFor(() => {
      expect(screen.queryByText("Mine")).toBeNull();
    });
    expect(screen.getByText("Free")).toBeDefined();
  });

  it("filters the task list by holder too", async () => {
    window.history.replaceState(null, "", "/projects/project-1/tasks");
    stubFetch();
    const AppShell = await loadAppShell();

    render(<AppShell />);
    expect(await screen.findByText("Theirs")).toBeDefined();

    await pickAssignee(ada);

    await waitFor(() => {
      expect(screen.queryByText("Theirs")).toBeNull();
    });
    expect(screen.getByText("Mine")).toBeDefined();
    expect(screen.getByText("1 of 3 tasks")).toBeDefined();
  });

  it("offers each holder once, however many cards they hold", async () => {
    window.history.replaceState(null, "", "/projects/project-1/board");
    stubFetch();
    const AppShell = await loadAppShell();

    render(<AppShell />);
    await screen.findByText("Mine");

    expect(
      (await assigneeOptions()).map((option) => option.textContent),
    ).toEqual(["All assignees", "Unassigned", ada, grace]);
  });
});
