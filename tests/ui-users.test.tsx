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

import { UsersDialog } from "../src/client/Users.js";
import type { Project, UserSummary } from "../src/shared/types.js";

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
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const project = (
  id: string,
  key: string,
  title: string,
  archivedAt?: string,
): Project => ({
  id,
  key,
  title,
  summary: "",
  createdAt: "",
  updatedAt: "",
  archivedAt,
});

const projects = [
  project("project-1", "APP", "App"),
  project("project-2", "API", "Api"),
  project("project-3", "OLD", "Old", "2026-10-01T10:00:00.000Z"),
];

const user = (overrides: Partial<UserSummary> = {}): UserSummary => ({
  id: "user-1",
  username: "rebecca",
  active: true,
  mustChangePassword: true,
  projectIds: ["project-1"],
  createdAt: "2026-11-16T09:00:00.000Z",
  ...overrides,
});

/** A users API that keeps its list in memory, like the server keeps it in the database. */
const stubUsersFetch = (initial: UserSummary[]) => {
  let users = [...initial];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (path === "/api/users" && method === "GET") {
      return json({ items: users });
    }
    if (path === "/api/users" && method === "POST") {
      const body = JSON.parse(init?.body as string);
      const created = user({
        id: "user-2",
        username: body.username,
        projectIds: body.projectIds,
      });
      users = [...users, created];
      return json(
        { item: created, temporaryPassword: "temp-password-01" },
        201,
      );
    }
    const match = path.match(/^\/api\/users\/([^/]+)(\/reset-password)?$/);
    if (!match) return json({ error: "Not found" }, 404);
    const [, id, reset] = match;
    const found = users.find((item) => item.id === id);
    if (!found) return json({ error: `User not found: ${id}` }, 404);
    if (reset && method === "POST") {
      const item = { ...found, mustChangePassword: true };
      users = users.map((other) => (other.id === id ? item : other));
      return json({ item, temporaryPassword: "temp-password-02" });
    }
    if (method === "PATCH") {
      const item = { ...found, ...JSON.parse(init?.body as string) };
      users = users.map((other) => (other.id === id ? item : other));
      return json({ item });
    }
    if (method === "DELETE") {
      users = users.filter((other) => other.id !== id);
      return json({ id, username: found.username });
    }
    return json({ error: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

/** The bodies of the requests that were sent with `method` to `path`. */
const sentBodies = (
  fetchMock: ReturnType<typeof stubUsersFetch>,
  method: string,
  path: string,
) =>
  fetchMock.mock.calls
    .filter(([url, init]) => url === path && init?.method === method)
    .map(([, init]) => (init?.body ? JSON.parse(init.body as string) : null));

const openDialog = () =>
  render(
    <MantineProvider>
      <UsersDialog opened onClose={vi.fn()} projects={projects} />
    </MantineProvider>,
  );

/** The card of one user. */
const card = async (username: string) =>
  within(await screen.findByRole("region", { name: `User ${username}` }));

describe("UsersDialog", () => {
  it("shows each user's projects, password state and last sign-in", async () => {
    stubUsersFetch([
      user(),
      user({
        id: "user-2",
        username: "jonas",
        active: false,
        mustChangePassword: false,
        projectIds: ["project-2", "project-3"],
        lastLoginAt: "2026-11-15T18:30:00.000Z",
        passwordChangedAt: "2026-11-15T18:31:00.000Z",
      }),
    ]);
    openDialog();

    const rebecca = await card("rebecca");
    expect(rebecca.getByText("Temporary password")).toBeTruthy();
    expect(rebecca.getByText(/Never signed in/)).toBeTruthy();
    expect(rebecca.getByText("APP")).toBeTruthy();
    expect(rebecca.queryByText("Deactivated")).toBeNull();
    expect(rebecca.getByRole("button", { name: "Deactivate" })).toBeTruthy();

    const jonas = await card("jonas");
    expect(jonas.getByText("Password set")).toBeTruthy();
    expect(jonas.getByText(/Last sign-in/)).toBeTruthy();
    expect(jonas.getByText("Deactivated")).toBeTruthy();
    expect(jonas.getByText("API")).toBeTruthy();
    expect(jonas.getByText("OLD (archived)")).toBeTruthy();
    expect(jonas.getByRole("button", { name: "Activate" })).toBeTruthy();
  });

  it("says so when there are no users", async () => {
    stubUsersFetch([]);
    openDialog();

    expect(await screen.findByText("No users yet.")).toBeTruthy();
  });

  it("adds a user and shows the temporary password once", async () => {
    const fetchMock = stubUsersFetch([]);
    openDialog();
    await screen.findByText("No users yet.");

    await userEvent.type(
      screen.getByRole("textbox", { name: /Username/ }),
      "jonas",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /Projects/ }),
      "API",
    );
    await userEvent.keyboard("{ArrowDown}{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Add User" }));

    expect(await screen.findByText("temp-password-01")).toBeTruthy();
    expect(screen.getByText("New User")).toBeTruthy();
    expect(sentBodies(fetchMock, "POST", "/api/users")).toEqual([
      { username: "jonas", projectIds: ["project-2"] },
    ]);
    expect((await card("jonas")).getByText("API")).toBeTruthy();
    expect(
      (screen.getByRole("textbox", { name: /Username/ }) as HTMLInputElement)
        .value,
    ).toBe("");
  });

  it("shows why a user could not be added", async () => {
    const fetchMock = stubUsersFetch([]);
    fetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/api/users" && init?.method === "POST") {
        return json({ error: "Username is taken: jonas" }, 409);
      }
      return json({ items: [] });
    });
    openDialog();
    await screen.findByText("No users yet.");

    await userEvent.type(
      screen.getByRole("textbox", { name: /Username/ }),
      "jonas",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add User" }));

    expect(await screen.findByText("Username is taken: jonas")).toBeTruthy();
  });

  it("changes the projects of a user", async () => {
    const fetchMock = stubUsersFetch([user()]);
    openDialog();

    const rebecca = await card("rebecca");
    await userEvent.click(
      rebecca.getByRole("button", { name: "Edit Projects" }),
    );
    await userEvent.type(
      rebecca.getByRole("textbox", { name: /Projects/ }),
      "API",
    );
    await userEvent.keyboard("{ArrowDown}{Enter}");
    await userEvent.click(
      rebecca.getByRole("button", { name: "Save Projects" }),
    );

    await waitFor(() =>
      expect(sentBodies(fetchMock, "PATCH", "/api/users/user-1")).toEqual([
        { projectIds: ["project-1", "project-2"] },
      ]),
    );
    expect(await (await card("rebecca")).findByText("API")).toBeTruthy();
    expect(
      (await card("rebecca")).queryByRole("button", { name: "Save Projects" }),
    ).toBeNull();
  });

  it("resets a password only after the admin confirmed", async () => {
    const fetchMock = stubUsersFetch([user({ mustChangePassword: false })]);
    openDialog();

    const rebecca = await card("rebecca");
    await userEvent.click(
      rebecca.getByRole("button", { name: "Reset Password" }),
    );

    expect(
      await screen.findByText(/current password stops working at once/),
    ).toBeTruthy();
    expect(
      sentBodies(fetchMock, "POST", "/api/users/user-1/reset-password"),
    ).toEqual([]);

    await userEvent.click(screen.getByRole("button", { name: "Reset Now" }));

    expect(await screen.findByText("temp-password-02")).toBeTruthy();
    expect(screen.getByText("Password Reset")).toBeTruthy();
    expect(
      (await card("rebecca")).getByText("Temporary password"),
    ).toBeTruthy();
  });

  it("deactivates and activates a user", async () => {
    const fetchMock = stubUsersFetch([user()]);
    openDialog();

    await userEvent.click(
      (await card("rebecca")).getByRole("button", { name: "Deactivate" }),
    );

    expect(
      await (await card("rebecca")).findByText("Deactivated"),
    ).toBeTruthy();
    expect(sentBodies(fetchMock, "PATCH", "/api/users/user-1")).toEqual([
      { active: false },
    ]);

    await userEvent.click(
      (await card("rebecca")).getByRole("button", { name: "Activate" }),
    );

    await waitFor(async () =>
      expect((await card("rebecca")).queryByText("Deactivated")).toBeNull(),
    );
  });

  it("deletes a user only after the admin confirmed", async () => {
    const fetchMock = stubUsersFetch([user()]);
    openDialog();

    await userEvent.click(
      (await card("rebecca")).getByRole("button", { name: "Delete" }),
    );
    expect(await screen.findByText(/Delete rebecca for good/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.queryByText(/Delete rebecca for good/)).toBeNull(),
    );
    expect(screen.getByRole("region", { name: "User rebecca" })).toBeTruthy();
    expect(sentBodies(fetchMock, "DELETE", "/api/users/user-1")).toEqual([]);

    await userEvent.click(
      (await card("rebecca")).getByRole("button", { name: "Delete" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Delete User" }),
    );

    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "User rebecca" })).toBeNull(),
    );
    expect(sentBodies(fetchMock, "DELETE", "/api/users/user-1")).toHaveLength(
      1,
    );
    expect(await screen.findByText("No users yet.")).toBeTruthy();
  });
});
