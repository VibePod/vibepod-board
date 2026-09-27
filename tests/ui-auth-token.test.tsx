// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiTokenSummary } from "../src/shared/types.js";

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
});

afterEach(() => {
  // Unmount first, so component effects and timers end while the stubs still exist.
  cleanup();
  vi.unstubAllGlobals();
  vi.resetModules();
});

const loadAppShell = async () => {
  const module = await import("../src/client/main.js");
  return module.AppShell;
};

const stubAdminFetch = (tokens: ApiTokenSummary[] = []) =>
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path === "/api/auth/me") {
        return new Response(
          JSON.stringify({ authenticated: true, username: "admin" }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        );
      }
      if (path === "/api/projects") {
        return new Response(
          JSON.stringify({
            items: [
              {
                id: "project-1",
                key: "APP",
                title: "App",
                summary: "",
                createdAt: "",
                updatedAt: "",
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (path === "/api/ideas") {
        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (path === "/api/board") {
        return new Response(
          JSON.stringify({
            columns: {
              ready: [],
              planned: [],
              in_progress: [],
              review: [],
              done: [],
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (path === "/api/documents") {
        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (path === "/api/tokens") {
        return new Response(JSON.stringify({ items: tokens }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("{}", { status: 404 });
    }),
  );

describe("admin auth UI", () => {
  it("shows login when the admin is unauthenticated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path === "/api/auth/me") {
          return new Response(JSON.stringify({ authenticated: false }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response("{}", { status: 404 });
      }),
    );

    const AppShell = await loadAppShell();
    render(<AppShell />);

    expect(await screen.findByLabelText("Username")).toBeTruthy();
    expect(await screen.findByLabelText("Password")).toBeTruthy();
  });

  it("opens token management for authenticated admins", async () => {
    stubAdminFetch();

    const AppShell = await loadAppShell();
    render(<AppShell />);
    await userEvent.click(
      await screen.findByRole("button", { name: "API Tokens" }),
    );

    expect(
      await screen.findByRole("button", { name: "Create Token" }),
    ).toBeTruthy();
  });

  it("shows the assigned projects for each token", async () => {
    stubAdminFetch([
      {
        id: "token-1",
        name: "Codex",
        projects: [{ id: "project-1", key: "APP", title: "App" }],
        createdAt: "2026-09-01T10:00:00Z",
      },
      {
        id: "token-2",
        name: "Old client",
        projects: [{ id: "project-2", key: "OPS", title: "Operations" }],
        createdAt: "2026-08-01T10:00:00Z",
        revokedAt: "2026-08-15T10:00:00Z",
      },
      {
        id: "token-3",
        name: "Orphan",
        projects: [],
        createdAt: "2026-07-01T10:00:00Z",
      },
    ]);

    const AppShell = await loadAppShell();
    render(<AppShell />);
    await userEvent.click(
      await screen.findByRole("button", { name: "API Tokens" }),
    );

    expect(await screen.findByText("APP · App")).toBeTruthy();
    expect(screen.getByText("OPS · Operations")).toBeTruthy();
    expect(screen.getByText("No projects")).toBeTruthy();
    expect(screen.getAllByText("Projects", { selector: "p" })).toHaveLength(3);
  });
});
