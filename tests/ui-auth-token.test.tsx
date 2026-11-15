// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiTokenSummary, PermissionArea } from "../src/shared/types.js";

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
  // jsdom has no layout; the project picker scrolls its active option into view.
  Element.prototype.scrollIntoView = vi.fn();
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

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const permissionAreas: PermissionArea[] = [
  {
    key: "tasks",
    label: "Tasks",
    description: "Tasks with their dependencies.",
    levels: ["none", "read", "write"],
    readTools: ["list_ideas"],
    writeTools: ["create_idea"],
  },
  {
    key: "board",
    label: "Board",
    description: "Board cards.",
    levels: ["none", "read", "write"],
    readTools: ["list_board"],
    writeTools: ["move_board_card"],
  },
  {
    key: "project",
    label: "Project",
    description: "Project settings.",
    levels: ["none", "write"],
    readTools: [],
    writeTools: ["update_project_settings"],
  },
];

const fullAccess = {
  tasks: "write",
  board: "write",
  project: "write",
} as const;

const token = (overrides: Partial<ApiTokenSummary> = {}): ApiTokenSummary => ({
  id: "token-1",
  name: "Codex",
  projects: [{ id: "project-1", key: "APP", title: "App" }],
  permissions: fullAccess,
  createdAt: "2026-09-01T10:00:00Z",
  ...overrides,
});

/** The body of the one request that was sent with `method` to `path`. */
const sentBody = (
  fetchMock: ReturnType<typeof vi.fn>,
  method: string,
  path: string,
) => {
  const calls = fetchMock.mock.calls.filter(
    ([url, init]) => url === path && (init as RequestInit)?.method === method,
  );
  expect(calls).toHaveLength(1);
  return JSON.parse((calls[0][1] as RequestInit).body as string);
};

const stubAdminFetch = (tokens: ApiTokenSummary[] = []) => {
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/permission-areas") {
      return json({ items: permissionAreas });
    }
    if (path === "/api/tokens" && init?.method === "POST") {
      const body = JSON.parse(init.body as string);
      return json(
        {
          item: token({ name: body.name, permissions: body.permissions }),
          token: "vbp_new",
        },
        201,
      );
    }
    if (path.startsWith("/api/tokens/") && init?.method === "PATCH") {
      return json({ item: token(JSON.parse(init.body as string)) });
    }
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
            pr_ready: [],
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
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

const openTokenManager = async () => {
  const AppShell = await loadAppShell();
  render(<AppShell />);
  await userEvent.click(
    await screen.findByRole("button", { name: "API Tokens" }),
  );
  await screen.findByRole("button", { name: "Create Token" });
};

/** The level control of one permission area. */
const levels = async (area: string) =>
  within(await screen.findByRole("radiogroup", { name: area }));

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

  it("links to the REST and MCP references from the integration guide", async () => {
    stubAdminFetch();

    const AppShell = await loadAppShell();
    render(<AppShell />);
    await userEvent.click(
      await screen.findByRole("button", { name: "MCP Integration" }),
    );

    const rest = await screen.findByRole("link", {
      name: "REST API reference",
    });
    const mcp = await screen.findByRole("link", { name: "MCP tool reference" });
    expect(rest.getAttribute("href")).toBe("/docs");
    expect(mcp.getAttribute("href")).toBe("/mcp-docs");
    expect(mcp.getAttribute("target")).toBe("_blank");
  });

  it("documents OAuth sign-in for Pi in the integration guide", async () => {
    stubAdminFetch();

    const AppShell = await loadAppShell();
    render(<AppShell />);
    await userEvent.click(
      await screen.findByRole("button", { name: "MCP Integration" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Pi with OAuth sign-in" }),
    ).toBeTruthy();
    expect(screen.getByText(/pi mcp login vibepod-board/)).toBeTruthy();
  });

  it("shows the assigned projects for each token", async () => {
    stubAdminFetch([
      token(),
      token({
        id: "token-2",
        name: "Old client",
        projects: [{ id: "project-2", key: "OPS", title: "Operations" }],
        createdAt: "2026-08-01T10:00:00Z",
        revokedAt: "2026-08-15T10:00:00Z",
      }),
      token({
        id: "token-3",
        name: "Orphan",
        projects: [],
        createdAt: "2026-07-01T10:00:00Z",
      }),
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

  it("shows the permissions of each token", async () => {
    stubAdminFetch([
      token(),
      token({
        id: "token-2",
        name: "Reader",
        permissions: { tasks: "read", board: "write", project: "none" },
      }),
      token({
        id: "token-3",
        name: "Locked out",
        permissions: { tasks: "none", board: "none", project: "none" },
      }),
    ]);

    await openTokenManager();

    const list = within(
      (await screen.findByText("Locked out")).closest(
        ".token-list",
      ) as HTMLElement,
    );
    expect(list.getByText("Full access")).toBeTruthy();
    expect(list.getByText("Tasks · Read")).toBeTruthy();
    expect(list.getByText("Board · Write")).toBeTruthy();
    expect(list.getByText("No access")).toBeTruthy();
  });

  it("offers each area the levels it has, starting from full access", async () => {
    stubAdminFetch();

    await openTokenManager();

    const tasks = await levels("Tasks");
    expect(
      tasks.getByRole<HTMLInputElement>("radio", { name: "Write" }).checked,
    ).toBe(true);
    expect(tasks.getByRole("radio", { name: "Read" })).toBeTruthy();
    const project = await levels("Project");
    expect(project.queryByRole("radio", { name: "Read" })).toBeNull();
    expect(screen.getByText("Tasks with their dependencies.")).toBeTruthy();
  });

  it("creates a token with the chosen permissions", async () => {
    const fetchMock = stubAdminFetch();

    await openTokenManager();
    await userEvent.type(screen.getByLabelText(/Token Name/), "Planner");
    // The page holds other project pickers, so the option is chosen by keyboard.
    await userEvent.click(screen.getByRole("textbox", { name: /Projects/ }));
    await userEvent.keyboard("{ArrowDown}{Enter}");
    await userEvent.click(
      (await levels("Board")).getByRole("radio", { name: "Read" }),
    );
    await userEvent.click(
      (await levels("Project")).getByRole("radio", { name: "None" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Create Token" }));

    expect(await screen.findByText("vbp_new")).toBeTruthy();
    expect(sentBody(fetchMock, "POST", "/api/tokens")).toEqual({
      name: "Planner",
      projectIds: ["project-1"],
      permissions: { tasks: "write", board: "read", project: "none" },
    });
  });

  it("sets every area to read with the read-only preset", async () => {
    stubAdminFetch();

    await openTokenManager();
    await userEvent.click(screen.getByRole("button", { name: "Read only" }));

    const checked = async (area: string, level: string) =>
      (await levels(area)).getByRole<HTMLInputElement>("radio", { name: level })
        .checked;
    expect(await checked("Tasks", "Read")).toBe(true);
    expect(await checked("Board", "Read")).toBe(true);
    expect(await checked("Project", "None")).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Full access" }));
    expect(await checked("Tasks", "Write")).toBe(true);
    expect(await checked("Project", "Write")).toBe(true);
  });

  it("changes the permissions of an existing token", async () => {
    const fetchMock = stubAdminFetch([
      token({ permissions: { tasks: "read", board: "none", project: "none" } }),
    ]);

    await openTokenManager();
    await userEvent.click(
      await screen.findByRole("button", { name: "Edit permissions" }),
    );
    const editor = within(
      await screen.findByRole("group", { name: "Permissions of Codex" }),
    );
    await userEvent.click(
      within(editor.getByRole("radiogroup", { name: "Board" })).getByRole(
        "radio",
        {
          name: "Write",
        },
      ),
    );
    await userEvent.click(
      editor.getByRole("button", { name: "Save permissions" }),
    );

    expect(sentBody(fetchMock, "PATCH", "/api/tokens/token-1")).toEqual({
      permissions: { tasks: "read", board: "write", project: "none" },
    });
    expect(
      screen.queryByRole("group", { name: "Permissions of Codex" }),
    ).toBeNull();
  });

  it("does not offer to edit a revoked token", async () => {
    stubAdminFetch([token({ revokedAt: "2026-09-02T10:00:00Z" })]);

    await openTokenManager();

    expect(await screen.findByText("Revoked")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Edit permissions" }),
    ).toBeNull();
  });
});
