// @vitest-environment jsdom

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const timestamp = "2026-08-14T12:00:00.000Z";
const project = {
  id: "project-1",
  key: "APP",
  title: "Current Application",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};

const existingIdea = {
  id: "idea-1",
  projectId: "project-1",
  taskNumber: 1,
  title: "Existing task",
  summary: "",
  details: "",
  status: "idea",
  labels: [],
  acceptanceCriteria: [],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  createdAt: timestamp,
  updatedAt: timestamp,
};

const emptyColumns = {
  ready: [],
  planned: [],
  in_progress: [],
  review: [],
  done: [],
};

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

/** Board state reads. `ideas` is a live array so a create can push into it. */
const stateResponse = (path: string, ideas: unknown[]) => {
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
    return jsonResponse({ columns: emptyColumns });
  }
  return undefined;
};

beforeEach(() => {
  window.history.replaceState(null, "", "/projects/project-1/tasks");
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

const openAddTask = async () => {
  await userEvent.click(
    await screen.findByRole("button", { name: "Add Task" }),
  );
  return screen.findByRole("dialog");
};

const postCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter(
    ([path, init]) =>
      String(path) === "/api/ideas" &&
      (init as RequestInit | undefined)?.method === "POST",
  );

describe("task modal saving", () => {
  it("creates the task once when the same draft is saved twice", async () => {
    const ideas: unknown[] = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === "/api/ideas" && init?.method === "POST") {
          const created = {
            ...existingIdea,
            id: "idea-new",
            taskNumber: 2,
            title: "Fresh task",
          };
          ideas.push(created);
          return jsonResponse({ item: created }, 201);
        }
        if (path === "/api/ideas/idea-new" && init?.method === "PATCH") {
          return jsonResponse({ item: ideas[0] });
        }
        return (
          stateResponse(path, ideas) ??
          jsonResponse({ error: "Not found" }, 404)
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const AppShell = await loadAppShell();
    render(<AppShell />);

    const dialog = await openAddTask();
    await userEvent.type(await screen.findByLabelText(/^Title/), "Fresh task");
    await userEvent.click(
      await within(dialog).findByRole("button", { name: "Save" }),
    );
    await waitFor(() => expect(postCalls(fetchMock)).toHaveLength(1));

    // The modal stays open. A second save must update the task just created
    // rather than posting another one.
    await userEvent.click(
      await within(dialog).findByRole("button", { name: "Save" }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([path, init]) =>
            String(path) === "/api/ideas/idea-new" &&
            (init as RequestInit | undefined)?.method === "PATCH",
        ),
      ).toBe(true),
    );
    expect(postCalls(fetchMock)).toHaveLength(1);
  });

  it("sends the assignee typed into the modal", async () => {
    const ideas: unknown[] = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === "/api/ideas" && init?.method === "POST") {
          const created = { ...existingIdea, id: "idea-new", taskNumber: 2 };
          ideas.push(created);
          return jsonResponse({ item: created }, 201);
        }
        return (
          stateResponse(path, ideas) ??
          jsonResponse({ error: "Not found" }, 404)
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const AppShell = await loadAppShell();
    render(<AppShell />);

    const dialog = await openAddTask();
    await userEvent.type(await screen.findByLabelText(/^Title/), "Claimed");
    await userEvent.type(
      await screen.findByLabelText(/^Assignee/),
      "Claude::Subagent101::Worktree12",
    );
    await userEvent.click(
      await within(dialog).findByRole("button", { name: "Save" }),
    );

    await waitFor(() => expect(postCalls(fetchMock)).toHaveLength(1));
    const [, init] = postCalls(fetchMock)[0];
    expect(JSON.parse(String((init as RequestInit).body))).toMatchObject({
      assignee: "Claude::Subagent101::Worktree12",
    });
  });

  it("reports a failed save instead of rejecting silently", async () => {
    const ideas: unknown[] = [existingIdea];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === "/api/ideas" && init?.method === "POST") {
          return jsonResponse({ error: "Title is required" }, 400);
        }
        return (
          stateResponse(path, ideas) ??
          jsonResponse({ error: "Not found" }, 404)
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const AppShell = await loadAppShell();
    render(<AppShell />);

    const dialog = await openAddTask();
    await userEvent.type(await screen.findByLabelText(/^Title/), "Doomed");
    await userEvent.click(
      await within(dialog).findByRole("button", { name: "Save" }),
    );

    expect(await screen.findByText("Title is required")).toBeTruthy();
  });
});

describe("task modal closing", () => {
  it("keeps the modal open when a discard confirmation is declined", async () => {
    const ideas: unknown[] = [existingIdea];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      return (
        stateResponse(path, ideas) ?? jsonResponse({ error: "Not found" }, 404)
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const confirmMock = vi.fn(() => false);
    vi.stubGlobal("confirm", confirmMock);
    const AppShell = await loadAppShell();
    render(<AppShell />);

    await userEvent.click(await screen.findByText("Existing task"));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(await screen.findByLabelText(/^Title/), " edited");

    await userEvent.click(
      await within(dialog).findByRole("button", { name: "Close" }),
    );
    expect(confirmMock).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeNull();

    confirmMock.mockReturnValue(true);
    await userEvent.click(
      await within(dialog).findByRole("button", { name: "Close" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("closes an untouched create dialog without confirming", async () => {
    const ideas: unknown[] = [existingIdea];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      return (
        stateResponse(path, ideas) ?? jsonResponse({ error: "Not found" }, 404)
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const confirmMock = vi.fn(() => true);
    vi.stubGlobal("confirm", confirmMock);
    const AppShell = await loadAppShell();
    render(<AppShell />);

    const dialog = await openAddTask();
    await userEvent.click(
      await within(dialog).findByRole("button", { name: "Close" }),
    );

    expect(confirmMock).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
