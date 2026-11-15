import { readFileSync } from "node:fs";
import { join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const page = readFileSync(
  join(process.cwd(), "backend/vibepod_board/static/oauth-consent.html"),
  "utf8",
);

type Reply = { status: number; body: unknown };
type Routes = Record<string, Reply | ((body: unknown) => Reply)>;

const REDIRECT = "http://127.0.0.1:4567/callback";

const waiting: Reply = {
  status: 200,
  body: {
    item: {
      id: "req-1",
      clientName: "pi",
      redirectUri: REDIRECT,
      expiresAt: "2026-11-19T15:10:00.000Z",
    },
  },
};

const twoProjects: Reply = {
  status: 200,
  body: {
    items: [
      { id: "p-app", key: "APP", title: "App" },
      { id: "p-ops", key: "OPS", title: "Operations" },
    ],
  },
};

const areas: Reply = {
  status: 200,
  body: {
    items: [
      {
        key: "tasks",
        label: "Tasks",
        description: "Tasks with their dependencies.",
        levels: ["none", "read", "write"],
      },
      {
        key: "board",
        label: "Board",
        description: "Board cards.",
        levels: ["none", "read", "write"],
      },
      {
        key: "project",
        label: "Project",
        description: "Project settings.",
        levels: ["none", "write"],
      },
    ],
  },
};

const signedIn: Reply = {
  status: 200,
  body: { authenticated: true, username: "admin" },
};

const signedOut: Reply = { status: 200, body: { authenticated: false } };

/**
 * Opens the consent page in jsdom; `routes` answers `fetch` by "METHOD path". The
 * permission areas are served unless a test replaces them.
 */
const openPage = async (given: Routes, search = "?request=req-1") => {
  const routes: Routes = { "GET /api/permission-areas": areas, ...given };
  const calls: { key: string; body: unknown }[] = [];
  const { window } = new JSDOM(page, {
    runScripts: "dangerously",
    url: `http://localhost:3000/oauth/consent${search}`,
    // Following the redirect is a navigation, which jsdom reports instead of performing.
    virtualConsole: new VirtualConsole(),
    beforeParse(pageWindow) {
      Object.assign(pageWindow, {
        fetch: async (path: string, options: RequestInit = {}) => {
          const key = `${options.method ?? "GET"} ${path}`;
          const body = options.body ? JSON.parse(String(options.body)) : null;
          calls.push({ key, body });
          const route = routes[key];
          const reply =
            typeof route === "function"
              ? route(body)
              : (route ?? { status: 404, body: { error: `No route ${key}` } });
          return {
            ok: reply.status < 400,
            status: reply.status,
            json: async () => reply.body,
          };
        },
      });
    },
  });
  const { document } = window;
  const byId = <T extends HTMLElement>(id: string) =>
    document.getElementById(id) as T;
  const status = byId("status");
  await vi.waitFor(() => {
    expect(status.textContent).not.toBe("Loading…");
  });
  return { window, document, byId, status, calls };
};

const submit = (window: JSDOM["window"], form: HTMLElement) => {
  form.dispatchEvent(
    new window.Event("submit", { bubbles: true, cancelable: true }),
  );
};

const tick = (window: JSDOM["window"], box: HTMLInputElement) => {
  box.checked = !box.checked;
  box.dispatchEvent(new window.Event("change", { bubbles: true }));
};

/** The radio for `level` of the permission area `area`, if the area offers it. */
const level = (document: Document, area: string, name: string) =>
  document.querySelector<HTMLInputElement>(
    `#permissions input[name="permission-${area}"][value="${name}"]`,
  );

describe("OAuth consent page", () => {
  it("asks a signed-in admin which projects the client may use", async () => {
    const { document, byId } = await openPage({
      "GET /api/auth/me": signedIn,
      "GET /api/oauth/requests/req-1": waiting,
      "GET /api/projects": twoProjects,
    });

    expect(byId("consent").hidden).toBe(false);
    expect(byId("login").hidden).toBe(true);
    expect(byId("client-name").textContent).toBe("pi");
    expect(byId("redirect").textContent).toBe(REDIRECT);
    expect(
      [...document.querySelectorAll("#projects label")].map((label) =>
        label.textContent?.trim(),
      ),
    ).toEqual(["APP · App", "OPS · Operations"]);
    expect(byId<HTMLButtonElement>("approve").disabled).toBe(true);
  });

  it("approves the ticked projects and returns to the client", async () => {
    const redirectTo = `${REDIRECT}?code=one-time&state=st4te`;
    const { window, document, byId, status, calls } = await openPage({
      "GET /api/auth/me": signedIn,
      "GET /api/oauth/requests/req-1": waiting,
      "GET /api/projects": twoProjects,
      "POST /api/oauth/requests/req-1/approve": {
        status: 200,
        body: { redirectTo },
      },
    });
    const boxes = [
      ...document.querySelectorAll<HTMLInputElement>("#projects input"),
    ];

    tick(window, boxes[1]);
    expect(byId<HTMLButtonElement>("approve").disabled).toBe(false);
    submit(window, byId("consent"));

    await vi.waitFor(() => {
      expect(status.querySelector("a")?.getAttribute("href")).toBe(redirectTo);
    });
    expect(status.textContent).toContain("Approved");
    expect(byId("consent").hidden).toBe(true);
    expect(calls.at(-1)).toEqual({
      key: "POST /api/oauth/requests/req-1/approve",
      body: {
        projectIds: ["p-ops"],
        permissions: { tasks: "write", board: "write", project: "write" },
      },
    });
  });

  it("offers every area with its levels, starting from full access", async () => {
    const { document } = await openPage({
      "GET /api/auth/me": signedIn,
      "GET /api/oauth/requests/req-1": waiting,
      "GET /api/projects": twoProjects,
    });

    expect(
      [...document.querySelectorAll("#permissions tbody th")].map(
        (cell) => cell.firstChild?.textContent,
      ),
    ).toEqual(["Tasks", "Board", "Project"]);
    expect(document.querySelector("#permissions tbody")?.textContent).toContain(
      "Tasks with their dependencies.",
    );
    expect(level(document, "tasks", "write")?.checked).toBe(true);
    expect(level(document, "tasks", "read")?.checked).toBe(false);
    expect(level(document, "tasks", "read")?.getAttribute("aria-label")).toBe(
      "Tasks: Read",
    );
    expect(level(document, "project", "write")?.checked).toBe(true);
    expect(level(document, "project", "read")).toBeNull();
  });

  it("approves with the permissions the admin chose", async () => {
    const redirectTo = `${REDIRECT}?code=one-time&state=st4te`;
    const { window, document, byId, status, calls } = await openPage({
      "GET /api/auth/me": signedIn,
      "GET /api/oauth/requests/req-1": waiting,
      "GET /api/projects": twoProjects,
      "POST /api/oauth/requests/req-1/approve": {
        status: 200,
        body: { redirectTo },
      },
    });

    tick(
      window,
      document.querySelector<HTMLInputElement>(
        "#projects input",
      ) as HTMLInputElement,
    );
    (level(document, "tasks", "read") as HTMLInputElement).click();
    (level(document, "project", "none") as HTMLInputElement).click();
    submit(window, byId("consent"));

    await vi.waitFor(() => {
      expect(status.textContent).toContain("Approved");
    });
    expect(calls.at(-1)?.body).toEqual({
      projectIds: ["p-app"],
      permissions: { tasks: "read", board: "write", project: "none" },
    });
  });

  it("offers a set for each project once two projects are ticked", async () => {
    const redirectTo = `${REDIRECT}?code=one-time&state=st4te`;
    const { window, document, byId, status, calls } = await openPage({
      "GET /api/auth/me": signedIn,
      "GET /api/oauth/requests/req-1": waiting,
      "GET /api/projects": twoProjects,
      "POST /api/oauth/requests/req-1/approve": {
        status: 200,
        body: { redirectTo },
      },
    });
    const boxes = [
      ...document.querySelectorAll<HTMLInputElement>("#projects input"),
    ];
    const option = byId("individual-option");
    const individual = byId<HTMLInputElement>("individual");

    tick(window, boxes[0]);
    expect(option.hidden).toBe(true);
    tick(window, boxes[1]);
    expect(option.hidden).toBe(false);

    // The shared choice becomes the starting point of every project.
    (level(document, "tasks", "read") as HTMLInputElement).click();
    tick(window, individual);
    expect(
      [...document.querySelectorAll("#permission-tables caption")].map(
        (caption) => caption.textContent,
      ),
    ).toEqual(["APP · App", "OPS · Operations"]);
    const ofProject = (project: string, area: string, name: string) =>
      document.querySelector<HTMLInputElement>(
        `#permission-tables input[data-project="${project}"][data-area="${area}"][value="${name}"]`,
      ) as HTMLInputElement;
    expect(ofProject("p-app", "tasks", "read").checked).toBe(true);
    expect(ofProject("p-ops", "tasks", "read").checked).toBe(true);
    expect(ofProject("p-ops", "tasks", "read").getAttribute("aria-label")).toBe(
      "Tasks in OPS · Operations: Read",
    );

    ofProject("p-ops", "tasks", "write").click();
    ofProject("p-ops", "board", "none").click();
    submit(window, byId("consent"));

    await vi.waitFor(() => {
      expect(status.textContent).toContain("Approved");
    });
    expect(calls.at(-1)?.body).toEqual({
      projectIds: ["p-app", "p-ops"],
      projectPermissions: {
        "p-app": { tasks: "read", board: "write", project: "write" },
        "p-ops": { tasks: "write", board: "none", project: "write" },
      },
    });
  });

  it("goes back to one set when fewer than two projects stay ticked", async () => {
    const { window, document, byId, calls } = await openPage({
      "GET /api/auth/me": signedIn,
      "GET /api/oauth/requests/req-1": waiting,
      "GET /api/projects": twoProjects,
      "POST /api/oauth/requests/req-1/approve": {
        status: 200,
        body: { redirectTo: `${REDIRECT}?code=one-time` },
      },
    });
    const boxes = [
      ...document.querySelectorAll<HTMLInputElement>("#projects input"),
    ];

    tick(window, boxes[0]);
    tick(window, boxes[1]);
    tick(window, byId<HTMLInputElement>("individual"));
    (
      document.querySelector(
        '#permission-tables input[data-project="p-ops"][data-area="tasks"][value="read"]',
      ) as HTMLInputElement
    ).click();
    tick(window, boxes[0]);

    // One project is left: its set carries on in the single table.
    expect(byId("individual-option").hidden).toBe(true);
    expect(document.querySelectorAll("#permission-tables table")).toHaveLength(
      1,
    );
    expect(level(document, "tasks", "read")?.checked).toBe(true);

    submit(window, byId("consent"));
    await vi.waitFor(() => {
      expect(calls.at(-1)?.key).toBe("POST /api/oauth/requests/req-1/approve");
    });
    expect(calls.at(-1)?.body).toEqual({
      projectIds: ["p-ops"],
      permissions: { tasks: "read", board: "write", project: "write" },
    });
  });

  it("ticks the only project for you", async () => {
    const { document, byId } = await openPage({
      "GET /api/auth/me": signedIn,
      "GET /api/oauth/requests/req-1": waiting,
      "GET /api/projects": {
        status: 200,
        body: { items: [{ id: "p-app", key: "APP", title: "App" }] },
      },
    });

    expect(
      document.querySelector<HTMLInputElement>("#projects input")?.checked,
    ).toBe(true);
    expect(byId<HTMLButtonElement>("approve").disabled).toBe(false);
  });

  it("denies and returns to the client with the error", async () => {
    const redirectTo = `${REDIRECT}?error=access_denied&state=st4te`;
    const { byId, status, calls } = await openPage({
      "GET /api/auth/me": signedIn,
      "GET /api/oauth/requests/req-1": waiting,
      "GET /api/projects": twoProjects,
      "POST /api/oauth/requests/req-1/deny": {
        status: 200,
        body: { redirectTo },
      },
    });

    byId("deny").click();

    await vi.waitFor(() => {
      expect(status.querySelector("a")?.getAttribute("href")).toBe(redirectTo);
    });
    expect(status.textContent).toContain("Denied");
    expect(calls.at(-1)?.key).toBe("POST /api/oauth/requests/req-1/deny");
  });

  it("asks for the admin login first when nobody is signed in", async () => {
    let loggedIn = false;
    const { window, byId } = await openPage({
      "GET /api/auth/me": signedOut,
      "POST /api/auth/login": (body) => {
        const { username, password } = body as Record<string, string>;
        loggedIn = username === "admin" && password === "secret";
        return loggedIn
          ? signedIn
          : { status: 401, body: { error: "Invalid username or password" } };
      },
      "GET /api/oauth/requests/req-1": waiting,
      "GET /api/projects": twoProjects,
    });
    expect(byId("login").hidden).toBe(false);
    expect(byId("consent").hidden).toBe(true);

    byId<HTMLInputElement>("username").value = "admin";
    byId<HTMLInputElement>("password").value = "wrong";
    submit(window, byId("login"));
    await vi.waitFor(() => {
      expect(byId("login-error").textContent).toBe(
        "Invalid username or password",
      );
    });
    expect(byId("consent").hidden).toBe(true);

    byId<HTMLInputElement>("password").value = "secret";
    submit(window, byId("login"));
    await vi.waitFor(() => {
      expect(byId("consent").hidden).toBe(false);
    });
    expect(byId("login").hidden).toBe(true);
    expect(byId("client-name").textContent).toBe("pi");
  });

  it("explains an expired or missing request instead of offering a decision", async () => {
    const expired = await openPage({
      "GET /api/auth/me": signedIn,
      "GET /api/oauth/requests/req-1": {
        status: 404,
        body: { error: "Sign-in request not found or expired" },
      },
      "GET /api/projects": twoProjects,
    });
    expect(expired.status.textContent).toContain("Start the sign-in again");
    expect(expired.byId("consent").hidden).toBe(true);
    expect(expired.byId("login").hidden).toBe(true);

    const missing = await openPage({ "GET /api/auth/me": signedIn }, "");
    expect(missing.status.textContent).toContain("Start the sign-in");
    expect(missing.calls).toEqual([]);
  });

  it("shows the client's name as text, never as markup", async () => {
    const { byId } = await openPage({
      "GET /api/auth/me": signedIn,
      "GET /api/oauth/requests/req-1": {
        status: 200,
        body: {
          item: {
            id: "req-1",
            clientName: "<img src=x onerror=alert(1)>",
            redirectUri: REDIRECT,
            expiresAt: "2026-11-19T15:10:00.000Z",
          },
        },
      },
      "GET /api/projects": twoProjects,
    });

    expect(byId("client-name").textContent).toBe(
      "<img src=x onerror=alert(1)>",
    );
    expect(byId("client-name").querySelector("img")).toBeNull();
  });
});
