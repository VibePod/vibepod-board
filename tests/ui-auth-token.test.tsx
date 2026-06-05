// @vitest-environment jsdom
import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
      dispatchEvent: vi.fn()
    }))
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
  vi.resetModules();
  document.body.innerHTML = "";
});

const loadAppShell = async () => {
  const module = await import("../src/client/main.js");
  return module.AppShell;
};

describe("admin auth UI", () => {
  it("shows login when the admin is unauthenticated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path === "/api/auth/me") {
          return new Response(JSON.stringify({ authenticated: false }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
          });
        }
        return new Response("{}", { status: 404 });
      })
    );

    const AppShell = await loadAppShell();
    render(<AppShell />);

    expect(await screen.findByLabelText("Username")).toBeTruthy();
    expect(await screen.findByLabelText("Password")).toBeTruthy();
  });

  it("opens token management for authenticated admins", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path === "/api/auth/me") {
          return new Response(JSON.stringify({ authenticated: true, username: "admin" }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
          });
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
                  updatedAt: ""
                }
              ]
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }
        if (path === "/api/ideas") {
          return new Response(JSON.stringify({ items: [] }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
          });
        }
        if (path === "/api/board") {
          return new Response(
            JSON.stringify({ columns: { ready: [], planned: [], in_progress: [], review: [], done: [] } }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }
        if (path === "/api/documents") {
          return new Response(JSON.stringify({ items: [] }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
          });
        }
        if (path === "/api/tokens") {
          return new Response(JSON.stringify({ items: [] }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
          });
        }
        return new Response("{}", { status: 404 });
      })
    );

    const AppShell = await loadAppShell();
    render(<AppShell />);
    await userEvent.click(await screen.findByRole("button", { name: "API Tokens" }));

    expect(await screen.findByRole("button", { name: "Create Token" })).toBeTruthy();
  });
});
