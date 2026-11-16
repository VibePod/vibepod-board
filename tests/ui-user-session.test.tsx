// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthMeResponse } from "../src/shared/types.js";

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
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
  Element.prototype.scrollIntoView = vi.fn();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetModules();
});

const loadAppShell = async () => {
  const module = await import("../src/client/main.js");
  return module.AppShell;
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const rebecca: AuthMeResponse = {
  authenticated: true,
  username: "rebecca",
  role: "user",
  mustChangePassword: false,
};

const admin: AuthMeResponse = {
  authenticated: true,
  username: "admin",
  role: "admin",
  mustChangePassword: false,
};

const emptyColumns = {
  ready: [],
  planned: [],
  in_progress: [],
  review: [],
  pr_ready: [],
  done: [],
};

const appProject = {
  id: "project-1",
  key: "APP",
  title: "App",
  summary: "",
  createdAt: "",
  updatedAt: "",
};

/**
 * A board with one project. `me` is who the session belongs to; `login` is what a
 * sign-in answers, and from then on `/api/auth/me` answers the same.
 */
const stubBoardFetch = (
  me: AuthMeResponse,
  {
    login,
    projects = [appProject],
  }: { login?: AuthMeResponse; projects?: unknown[] } = {},
) => {
  let current = me;
  const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
    if (path === "/api/auth/me") return json(current);
    if (path === "/api/auth/login" && login) {
      current = login;
      return json(login);
    }
    if (path === "/api/auth/password") {
      current = { ...current, mustChangePassword: false };
      return json(current);
    }
    if (path === "/api/projects") return json({ items: projects });
    if (path === "/api/ideas") return json({ items: [] });
    if (path === "/api/documents") return json({ items: [] });
    if (path === "/api/board") return json({ columns: emptyColumns });
    return json({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

const requested = (fetchMock: ReturnType<typeof vi.fn>, path: string) =>
  fetchMock.mock.calls.some(([url]) => url === path);

describe("a user's session", () => {
  it("hides what only the admin may do", async () => {
    stubBoardFetch(rebecca);
    const AppShell = await loadAppShell();
    render(<AppShell />);

    // The project card is there, so the lists below are complete.
    await screen.findByRole("link", { name: "Board" });

    for (const name of [
      "API Tokens",
      "Users",
      "Add Project",
      "Import Project",
      "Export",
      "Edit",
      "Archive",
    ]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(
      screen.queryByRole("link", { name: "Archived projects" }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Logout" })).toBeTruthy();
  });

  it("shows the admin everything", async () => {
    stubBoardFetch(admin);
    const AppShell = await loadAppShell();
    render(<AppShell />);

    await screen.findByRole("link", { name: "Board" });

    for (const name of [
      "API Tokens",
      "Add Project",
      "Import Project",
      "Export",
      "Edit",
      "Archive",
    ]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
    expect(
      screen.getByRole("link", { name: "Archived projects" }),
    ).toBeTruthy();
  });

  it("treats a session without a role as the admin's", async () => {
    stubBoardFetch({ authenticated: true, username: "admin" });
    const AppShell = await loadAppShell();
    render(<AppShell />);

    expect(
      await screen.findByRole("button", { name: "API Tokens" }),
    ).toBeTruthy();
  });

  it("tells a user without projects to ask the admin", async () => {
    stubBoardFetch(rebecca, { projects: [] });
    const AppShell = await loadAppShell();
    render(<AppShell />);

    expect(
      await screen.findByText(
        "No project is assigned to you yet. Ask the admin for access.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add Project" })).toBeNull();
  });

  it("signs a user in from the same form as the admin", async () => {
    const fetchMock = stubBoardFetch(
      { authenticated: false },
      { login: rebecca },
    );
    const AppShell = await loadAppShell();
    render(<AppShell />);

    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeTruthy();
    await userEvent.type(screen.getByLabelText("Username"), "rebecca");
    await userEvent.type(screen.getByLabelText("Password"), "her password");
    await userEvent.click(screen.getByRole("button", { name: "Sign In" }));

    await screen.findByRole("link", { name: "Board" });
    await waitFor(() =>
      expect(requested(fetchMock, "/api/projects")).toBe(true),
    );
    expect(screen.queryByRole("button", { name: "API Tokens" })).toBeNull();
  });
});
