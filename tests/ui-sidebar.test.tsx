// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sidebarStorageKey } from "../src/client/sidebarState.js";
import type { BoardColumns } from "../src/shared/types.js";

const timestamp = "2026-09-29T08:00:00.000Z";

const project = {
  id: "project-1",
  key: "VP",
  title: "VibePod",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};

const columns: BoardColumns = {
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

const boardApi = () =>
  vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/auth/me") {
      return jsonResponse({ authenticated: true, username: "admin" });
    }
    if (path === "/api/projects") return jsonResponse({ items: [project] });
    if (path === "/api/ideas") return jsonResponse({ items: [] });
    if (path === "/api/documents") return jsonResponse({ items: [] });
    if (path === "/api/github") return jsonResponse({ enabled: false });
    if (path === "/api/board") return jsonResponse({ columns });
    if (path === "/api/board/archived") return jsonResponse({ items: [] });
    return jsonResponse({ error: "Not found" }, 404);
  });

const mockScreen = ({ narrow }: { narrow: boolean }) => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: narrow && query.includes("max-width: 980px"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
};

const renderApp = async (path = `/projects/${project.id}/board`) => {
  window.history.replaceState(null, "", path);
  vi.stubGlobal("fetch", boardApi());
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  await screen.findByRole("navigation", { name: "Project" });
};

const sidebar = () =>
  screen.getByRole("navigation", { name: "Project" }).closest("aside");

beforeEach(() => {
  window.localStorage.clear();
  mockScreen({ narrow: false });
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

describe("collapsible sidebar", () => {
  it("collapses to an icon rail and expands again", async () => {
    await renderApp();
    const toggle = screen.getByRole("button", { name: "Collapse sidebar" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.queryByText("Current Project")).not.toBeNull();

    await userEvent.click(toggle);

    const expand = screen.getByRole("button", { name: "Expand sidebar" });
    expect(expand.getAttribute("aria-expanded")).toBe("false");
    expect(sidebar()?.classList.contains("is-collapsed")).toBe(true);
    const nav = screen.getByRole("navigation", { name: "Project" });
    for (const name of ["Tasks", "Board", "Archive", "Notes"]) {
      const link = within(nav).getByRole("link", { name });
      expect(link.getAttribute("aria-label")).toBe(name);
      expect(link.textContent).not.toContain(name);
    }
    expect(
      within(nav)
        .getByRole("link", { name: "Board" })
        ?.classList.contains("active"),
    ).toBe(true);
    expect(screen.queryByText("Current Project")).toBeNull();
    expect(
      screen.getByLabelText("Current project: VibePod").textContent,
    ).toContain("VP");

    await userEvent.click(expand);

    expect(
      screen
        .getByRole("button", { name: "Collapse sidebar" })
        .getAttribute("aria-expanded"),
    ).toBe("true");
    expect(sidebar()?.classList.contains("is-collapsed")).toBe(false);
    expect(
      within(nav).getByRole("link", { name: "Board" }).textContent,
    ).toContain("Board");
  });

  it("shows the item name as a tooltip in the rail", async () => {
    await renderApp();
    await userEvent.click(
      screen.getByRole("button", { name: "Collapse sidebar" }),
    );

    await userEvent.hover(screen.getByRole("link", { name: "Notes" }));

    expect((await screen.findByRole("tooltip")).textContent).toContain("Notes");
  });

  it("keeps rail links navigable", async () => {
    await renderApp();
    await userEvent.click(
      screen.getByRole("button", { name: "Collapse sidebar" }),
    );

    await userEvent.click(screen.getByRole("link", { name: "Notes" }));

    expect(window.location.pathname).toBe(`/projects/${project.id}/notes`);
    expect(
      screen.getByRole("link", { name: "Notes" })?.classList.contains("active"),
    ).toBe(true);
    expect(sidebar()?.classList.contains("is-collapsed")).toBe(true);
  });

  it("remembers the choice and applies it on the first render", async () => {
    await renderApp();
    await userEvent.click(
      screen.getByRole("button", { name: "Collapse sidebar" }),
    );
    expect(window.localStorage.getItem(sidebarStorageKey)).toBe("collapsed");

    cleanup();
    vi.resetModules();
    await renderApp();

    expect(sidebar()?.classList.contains("is-collapsed")).toBe(true);
    expect(
      screen
        .getByRole("button", { name: "Expand sidebar" })
        .getAttribute("aria-expanded"),
    ).toBe("false");

    await userEvent.click(
      screen.getByRole("button", { name: "Expand sidebar" }),
    );
    expect(window.localStorage.getItem(sidebarStorageKey)).toBe("expanded");
  });

  it("starts expanded when storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    await renderApp();

    await userEvent.click(
      screen.getByRole("button", { name: "Collapse sidebar" }),
    );

    expect(sidebar()?.classList.contains("is-collapsed")).toBe(true);
  });

  it("toggles with the [ key, but not while typing", async () => {
    await renderApp();

    await userEvent.keyboard("[[");
    expect(sidebar()?.classList.contains("is-collapsed")).toBe(true);

    await userEvent.keyboard("[[");
    expect(sidebar()?.classList.contains("is-collapsed")).toBe(false);

    await userEvent.click(screen.getByRole("textbox", { name: "Search" }));
    await userEvent.keyboard("[[");
    expect(sidebar()?.classList.contains("is-collapsed")).toBe(false);
  });

  it("is reachable and operable from the keyboard", async () => {
    await renderApp();
    const toggle = screen.getByRole("button", { name: "Collapse sidebar" });
    toggle.focus();

    await userEvent.keyboard("{Enter}");
    expect(sidebar()?.classList.contains("is-collapsed")).toBe(true);

    await userEvent.tab();
    expect(document.activeElement).toBe(
      screen.getByRole("link", { name: "Tasks" }),
    );
  });

  it("opens the menu as an overlay on narrow screens", async () => {
    mockScreen({ narrow: true });
    window.localStorage.setItem(sidebarStorageKey, "collapsed");
    await renderApp();

    const show = screen.getByRole("button", { name: "Show menu" });
    expect(show.getAttribute("aria-expanded")).toBe("false");
    expect(sidebar()?.classList.contains("is-collapsed")).toBe(false);
    expect(sidebar()?.classList.contains("is-open")).toBe(false);

    await userEvent.click(show);
    expect(sidebar()?.classList.contains("is-open")).toBe(true);
    expect(
      screen
        .getByRole("button", { name: "Hide menu" })
        .getAttribute("aria-expanded"),
    ).toBe("true");

    await userEvent.keyboard("{Escape}");
    expect(sidebar()?.classList.contains("is-open")).toBe(false);

    await userEvent.keyboard("[[");
    expect(sidebar()?.classList.contains("is-open")).toBe(true);
    await userEvent.click(screen.getByRole("link", { name: "Tasks" }));
    expect(sidebar()?.classList.contains("is-open")).toBe(false);
    // The overlay does not overwrite the remembered desktop choice.
    expect(window.localStorage.getItem(sidebarStorageKey)).toBe("collapsed");
  });
});
