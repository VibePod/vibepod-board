// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DeleteTaskDialog,
  deleteTaskConsequences,
} from "../src/client/DeleteTaskDialog.js";
import type { Idea } from "../src/shared/types.js";

const timestamp = "2026-08-14T12:00:00.000Z";
const project = {
  id: "project-1",
  key: "APP",
  title: "Current Application",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};

const idea = (patch: Partial<Idea> = {}): Idea => ({
  id: "idea-1",
  projectId: project.id,
  taskNumber: 7,
  title: "Doomed task",
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
  ...patch,
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

beforeEach(() => {
  window.history.replaceState(null, "", `/projects/${project.id}/tasks`);
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

describe("delete task consequences", () => {
  it("names dependents and the untouched GitHub issue", () => {
    expect(deleteTaskConsequences(idea())).toEqual([
      "Removes its board card, dependency links and readiness history.",
    ]);
    expect(
      deleteTaskConsequences(
        idea({
          blocks: ["a", "b"],
          githubIssueUrl: "https://github.com/o/r/issues/3",
          githubIssueNumber: 3,
          githubRepository: "o/r",
        }),
      ),
    ).toEqual([
      "Removes its board card, dependency links and readiness history.",
      "2 tasks depend on it and will lose this dependency.",
      "The linked GitHub issue o/r#3 is not touched.",
    ]);
  });
});

describe("delete task dialog", () => {
  it("does nothing on cancel and reports a failed delete", async () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn(async () => {
      throw new Error("Token is not allowed to access project: project-1");
    });
    render(
      <MantineProvider>
        <DeleteTaskDialog
          target={{ idea: idea(), taskId: "APP-7" }}
          onCancel={onCancel}
          onConfirm={onConfirm}
        />
      </MantineProvider>,
    );

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/cannot be undone/)).toBeTruthy();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Delete Task" }),
    );
    expect(
      await within(dialog).findByText(
        "Token is not allowed to access project: project-1",
      ),
    ).toBeTruthy();

    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cancel" }),
    );
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});

describe("deleting from the task view", () => {
  it("confirms, deletes through the API and reloads the list", async () => {
    let ideas: Idea[] = [idea()];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === "/api/ideas/idea-1" && init?.method === "DELETE") {
          ideas = [];
          return jsonResponse({
            id: "idea-1",
            taskId: "APP-7",
            dependents: [],
          });
        }
        if (path === "/api/auth/me") {
          return jsonResponse({ authenticated: true, username: "admin" });
        }
        if (path === "/api/projects") return jsonResponse({ items: [project] });
        if (path === "/api/ideas") return jsonResponse({ items: ideas });
        if (path === "/api/documents") return jsonResponse({ items: [] });
        if (path === "/api/github") return jsonResponse({ enabled: false });
        if (path === "/api/board") {
          return jsonResponse({
            columns: {
              ready: [],
              planned: [],
              in_progress: [],
              review: [],
              pr_ready: [],
              done: [],
            },
          });
        }
        return jsonResponse({ error: "Not found" }, 404);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const { AppShell } = await import("../src/client/main.js");
    render(<AppShell />);

    await userEvent.click(await screen.findByText("Doomed task"));
    const view = await screen.findByRole("dialog");
    await userEvent.click(within(view).getByRole("button", { name: "Delete" }));

    const confirm = await screen.findByRole("dialog", { name: "Delete Task" });
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Delete Task" }),
    );

    await waitFor(() => expect(screen.queryByText("Doomed task")).toBeNull());
    expect(await screen.findByText("Deleted APP-7.")).toBeTruthy();
    expect(
      fetchMock.mock.calls.filter(
        ([, init]) => (init as RequestInit | undefined)?.method === "DELETE",
      ),
    ).toHaveLength(1);
  });
});
