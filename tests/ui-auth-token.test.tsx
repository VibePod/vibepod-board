// @vitest-environment jsdom

import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ApiTokenSummary,
  PermissionArea,
  TokenPermissions,
} from "../src/shared/types.js";

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

const reader = { tasks: "read", board: "none", project: "none" } as const;

const app = (permissions: TokenPermissions = fullAccess) => ({
  id: "project-1",
  key: "APP",
  title: "App",
  permissions,
});

const api = (permissions: TokenPermissions = fullAccess) => ({
  id: "project-2",
  key: "API",
  title: "Api",
  permissions,
});

const token = (overrides: Partial<ApiTokenSummary> = {}): ApiTokenSummary => ({
  id: "token-1",
  name: "Codex",
  projects: [app()],
  createdAt: "2026-09-01T10:00:00Z",
  ...overrides,
});

const boardProject = (id: string, key: string, title: string) => ({
  id,
  key,
  title,
  summary: "",
  createdAt: "",
  updatedAt: "",
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

/** A promise a test settles when it wants a held-back response to arrive. */
const gate = () => {
  let open = () => {};
  const closed = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { closed, open };
};

const stubAdminFetch = (
  tokens: ApiTokenSummary[] = [],
  projects = [boardProject("project-1", "APP", "App")],
  // Hold back the permission areas, or the answer to a save, until the test lets go.
  held: { areas?: Promise<void>; save?: Promise<void> } = {},
) => {
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/permission-areas") {
      await held.areas;
      return json({ items: permissionAreas });
    }
    if (path === "/api/tokens" && init?.method === "POST") {
      const body = JSON.parse(init.body as string);
      return json(
        {
          item: token({ name: body.name, projects: [app(body.permissions)] }),
          token: "vbp_new",
        },
        201,
      );
    }
    if (path.startsWith("/api/tokens/") && init?.method === "PATCH") {
      await held.save;
      return json({ item: token() });
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
      return json({ items: projects });
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
        projects: [
          {
            id: "project-2",
            key: "OPS",
            title: "Operations",
            permissions: fullAccess,
          },
        ],
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
        projects: [app({ tasks: "read", board: "write", project: "none" })],
      }),
      token({
        id: "token-3",
        name: "Locked out",
        projects: [app({ tasks: "none", board: "none", project: "none" })],
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

  it("shows the permissions of each project when they differ", async () => {
    stubAdminFetch([token({ projects: [app(reader), api()] })]);

    await openTokenManager();

    const perProject = within(
      await screen.findByRole("list", { name: "Permissions by project" }),
    );
    expect(
      perProject.getAllByRole("listitem").map((item) => item.textContent),
    ).toEqual(["APP · AppTasks · Read", "API · ApiFull access"]);
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
    // One project: nothing to set individually.
    expect(
      screen.queryByRole("switch", {
        name: "Set permissions for each project",
      }),
    ).toBeNull();
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

  const editAccess = async (name: string) => {
    await userEvent.click(
      await screen.findByRole("button", { name: "Edit access" }),
    );
    return within(
      await screen.findByRole("group", { name: `Access of ${name}` }),
    );
  };

  it("changes the permissions of an existing token", async () => {
    const fetchMock = stubAdminFetch([token({ projects: [app(reader)] })]);

    await openTokenManager();
    const editor = await editAccess("Codex");
    await userEvent.click(
      within(editor.getByRole("radiogroup", { name: "Board" })).getByRole(
        "radio",
        { name: "Write" },
      ),
    );
    await userEvent.click(editor.getByRole("button", { name: "Save access" }));

    expect(sentBody(fetchMock, "PATCH", "/api/tokens/token-1")).toEqual({
      projectIds: ["project-1"],
      permissions: { tasks: "read", board: "write", project: "none" },
    });
    expect(screen.queryByRole("group", { name: "Access of Codex" })).toBeNull();
  });

  it("adds a project to an existing token", async () => {
    const fetchMock = stubAdminFetch(
      [token({ projects: [app(reader)] })],
      [
        boardProject("project-1", "APP", "App"),
        boardProject("project-2", "API", "Api"),
      ],
    );

    await openTokenManager();
    const editor = await editAccess("Codex");
    await userEvent.type(
      editor.getByRole("textbox", { name: /Projects/ }),
      "API",
    );
    await userEvent.keyboard("{ArrowDown}{Enter}");
    await userEvent.click(editor.getByRole("button", { name: "Save access" }));

    expect(sentBody(fetchMock, "PATCH", "/api/tokens/token-1")).toEqual({
      projectIds: ["project-1", "project-2"],
      permissions: reader,
    });
  });

  it("sets the permissions of each project individually", async () => {
    const fetchMock = stubAdminFetch(
      [token({ projects: [app(reader), api(reader)] })],
      [
        boardProject("project-1", "APP", "App"),
        boardProject("project-2", "API", "Api"),
      ],
    );

    await openTokenManager();
    const editor = await editAccess("Codex");
    // The projects agree, so they start with one shared table.
    expect(editor.getAllByRole("radiogroup", { name: "Tasks" })).toHaveLength(
      1,
    );
    await userEvent.click(
      editor.getByRole("switch", { name: "Set permissions for each project" }),
    );
    await userEvent.click(
      within(
        editor.getByRole("radiogroup", { name: "Tasks in API - Api" }),
      ).getByRole("radio", { name: "Write" }),
    );
    await userEvent.click(editor.getByRole("button", { name: "Save access" }));

    expect(sentBody(fetchMock, "PATCH", "/api/tokens/token-1")).toEqual({
      projectIds: ["project-1", "project-2"],
      projectPermissions: {
        "project-1": reader,
        "project-2": { tasks: "write", board: "none", project: "none" },
      },
    });
  });

  it("opens a token whose projects differ project by project", async () => {
    stubAdminFetch(
      [token({ projects: [app(reader), api()] })],
      [
        boardProject("project-1", "APP", "App"),
        boardProject("project-2", "API", "Api"),
      ],
    );

    await openTokenManager();
    const editor = await editAccess("Codex");

    const individually = editor.getByRole<HTMLInputElement>("switch", {
      name: "Set permissions for each project",
    });
    expect(individually.checked).toBe(true);
    expect(
      within(
        editor.getByRole("radiogroup", { name: "Tasks in APP - App" }),
      ).getByRole<HTMLInputElement>("radio", { name: "Read" }).checked,
    ).toBe(true);
    expect(
      within(
        editor.getByRole("radiogroup", { name: "Tasks in API - Api" }),
      ).getByRole<HTMLInputElement>("radio", { name: "Write" }).checked,
    ).toBe(true);
  });

  it("lets a token be edited only once the permission areas are loaded", async () => {
    const areas = gate();
    stubAdminFetch([token({ projects: [app(reader)] })], undefined, {
      areas: areas.closed,
    });

    await openTokenManager();
    const edit = await screen.findByRole<HTMLButtonElement>("button", {
      name: "Edit access",
    });
    // The token is listed, but what its levels stand for is not known yet. An editor
    // opened now would show no access everywhere and save that over the real grants.
    expect(edit.disabled).toBe(true);

    areas.open();
    await waitFor(() => expect(edit.disabled).toBe(false));
    await userEvent.click(edit);
    const editor = within(
      await screen.findByRole("group", { name: "Access of Codex" }),
    );
    expect(
      within(
        editor.getByRole("radiogroup", { name: "Tasks" }),
      ).getByRole<HTMLInputElement>("radio", { name: "Read" }).checked,
    ).toBe(true);
  });

  it("keeps the editor of another token open when an earlier save completes", async () => {
    const save = gate();
    const fetchMock = stubAdminFetch(
      [token(), token({ id: "token-2", name: "Pi" })],
      undefined,
      { save: save.closed },
    );
    const listLoads = () =>
      fetchMock.mock.calls.filter(
        ([url, init]) =>
          url === "/api/tokens" && !(init as RequestInit)?.method,
      ).length;
    const tasksOfPi = () =>
      within(
        within(screen.getByRole("group", { name: "Access of Pi" })).getByRole(
          "radiogroup",
          { name: "Tasks" },
        ),
      ).getByRole<HTMLInputElement>("radio", { name: "Read" });

    await openTokenManager();
    const [editCodex, editPi] = await screen.findAllByRole("button", {
      name: "Edit access",
    });
    await userEvent.click(editCodex);
    await userEvent.click(
      within(
        await screen.findByRole("group", { name: "Access of Codex" }),
      ).getByRole("button", { name: "Save access" }),
    );
    // The save is still under way when the admin turns to the next token.
    await userEvent.click(editPi);
    await screen.findByRole("group", { name: "Access of Pi" });
    await userEvent.click(tasksOfPi());

    save.open();
    await waitFor(() => expect(listLoads()).toBe(2));

    expect(tasksOfPi().checked).toBe(true);
  });

  it("does not offer to edit a revoked token", async () => {
    stubAdminFetch([token({ revokedAt: "2026-09-02T10:00:00Z" })]);

    await openTokenManager();

    expect(await screen.findByText("Revoked")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit access" })).toBeNull();
  });
});
