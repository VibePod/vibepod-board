// @vitest-environment jsdom

import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const timestamp = "2026-09-30T08:00:00.000Z";

const project = {
  id: "project-1",
  key: "VP",
  title: "VibePod",
  summary: "",
  requiredApprovals: 1,
  maxReviewRounds: 3,
  createdAt: timestamp,
  updatedAt: timestamp,
};

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const projectsApi = () =>
  vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/auth/me") {
      return jsonResponse({ authenticated: true, username: "admin" });
    }
    if (path === `/api/projects/${project.id}` && init?.method === "PATCH") {
      return jsonResponse({
        item: { ...project, ...JSON.parse(String(init.body)) },
      });
    }
    if (path === "/api/projects") return jsonResponse({ items: [project] });
    if (path === "/api/ideas") return jsonResponse({ items: [] });
    if (path === "/api/documents") return jsonResponse({ items: [] });
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
    if (path === "/api/board/archived") return jsonResponse({ items: [] });
    return jsonResponse({ error: "Not found" }, 404);
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
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("project review settings", () => {
  it("edits the required approvals and review rounds", async () => {
    window.history.replaceState(null, "", "/");
    const fetchMock = projectsApi();
    vi.stubGlobal("fetch", fetchMock);
    const { AppShell } = await import("../src/client/main.js");
    render(<AppShell />);

    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit Project" });
    const approvals = within(dialog).getByLabelText(/Required approvals/);
    const rounds = within(dialog).getByLabelText(/Review rounds/);
    expect((approvals as HTMLInputElement).value).toBe("1");
    expect((rounds as HTMLInputElement).value).toBe("3");

    fireEvent.change(approvals, { target: { value: "2" } });
    fireEvent.change(rounds, { target: { value: "4" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    const patch = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    );
    const body = (patch?.[1] as RequestInit | undefined)?.body;
    expect(JSON.parse(String(body))).toMatchObject({
      requiredApprovals: 2,
      maxReviewRounds: 4,
    });
  });
});
