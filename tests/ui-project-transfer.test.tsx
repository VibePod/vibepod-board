// @vitest-environment jsdom

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ProjectBundle } from "../src/shared/types.js";

const timestamp = "2026-08-14T12:00:00.000Z";
const destinationProject = {
  id: "project-1",
  key: "APP",
  title: "Current Application",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};
const bundle: ProjectBundle = {
  bundleVersion: 1,
  exportedAt: timestamp,
  project: {
    ...destinationProject,
    id: "source-project",
    title: "Application",
  },
  ideas: [
    {
      id: "idea-1",
      projectId: "source-project",
      taskNumber: 1,
      title: "One",
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
    },
    {
      id: "idea-2",
      projectId: "source-project",
      taskNumber: 2,
      title: "Two",
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
    },
  ],
  boardCards: [
    {
      id: "card-1",
      projectId: "source-project",
      ideaId: "idea-1",
      title: "One",
      details: "",
      column: "ready",
      labels: [],
      dependsOn: [],
      blockedBy: [],
      createdAt: timestamp,
      updatedAt: timestamp,
    },
  ],
  readinessEvents: [],
  documents: [
    {
      id: "document-1",
      projectId: "source-project",
      title: "Plan",
      kind: "execution_plan",
      content: "",
      linkedIdeaIds: ["idea-1"],
      linkedCardIds: ["card-1"],
      createdAt: timestamp,
      updatedAt: timestamp,
    },
  ],
};

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const emptyColumns = {
  ready: [],
  planned: [],
  in_progress: [],
  review: [],
  done: [],
};

const stateResponse = (path: string) => {
  if (path === "/api/auth/me") {
    return jsonResponse({ authenticated: true, username: "admin" });
  }
  if (path === "/api/projects") {
    return jsonResponse({ items: [destinationProject] });
  }
  if (path === "/api/ideas" || path === "/api/documents") {
    return jsonResponse({ items: [] });
  }
  if (path === "/api/board") {
    return jsonResponse({ columns: emptyColumns });
  }
  return undefined;
};

beforeEach(() => {
  window.history.replaceState(null, "", "/");
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
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:project-export"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    () => undefined,
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
  document.body.innerHTML = "";
});

const loadAppShell = async () => {
  const module = await import("../src/client/main.js");
  return module.AppShell;
};

describe("project transfer UI", () => {
  it("exports a project card as a JSON download", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      const state = stateResponse(path);
      if (state) return state;
      if (path === "/api/projects/project-1/export") {
        return jsonResponse(bundle);
      }
      return jsonResponse({ error: "Not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const AppShell = await loadAppShell();
    render(<AppShell />);

    await userEvent.click(
      await screen.findByRole("button", { name: "Export" }),
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/projects/project-1/export",
      expect.objectContaining({ credentials: "include" }),
    );
    expect(URL.createObjectURL).toHaveBeenCalledOnce();
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce();
  });

  it("previews and confirms replacement before refreshing state", async () => {
    let resolveImport: ((response: Response) => void) | undefined;
    const importResponse = new Promise<Response>((resolve) => {
      resolveImport = resolve;
    });
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === "/api/projects/import" && init?.method === "POST") {
          return importResponse;
        }
        return stateResponse(path) ?? jsonResponse({ error: "Not found" }, 404);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const AppShell = await loadAppShell();
    render(<AppShell />);
    await screen.findByText("Current Application");

    await userEvent.click(
      screen.getByRole("button", { name: "Import Project" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.upload(
      dialog.querySelector("input[type='file']") as HTMLInputElement,
      new File([JSON.stringify(bundle)], "APP-project.json", {
        type: "application/json",
      }),
    );

    expect(await within(dialog).findByText("Application (APP)")).toBeTruthy();
    expect(within(dialog).getByText("2 tasks")).toBeTruthy();
    expect(within(dialog).getByText("1 board card")).toBeTruthy();
    expect(within(dialog).getByText("1 document")).toBeTruthy();
    expect(
      within(dialog).getByText(/will replace all current project data/i),
    ).toBeTruthy();

    const replaceButton = within(dialog).getByRole("button", {
      name: "Replace Project",
    });
    await userEvent.click(replaceButton);
    expect((replaceButton as HTMLButtonElement).disabled).toBe(true);
    const importCall = fetchMock.mock.calls.find(
      ([path]) => path === "/api/projects/import",
    );
    expect(JSON.parse(String(importCall?.[1]?.body))).toEqual({
      bundle,
      replaceExisting: true,
    });

    resolveImport?.(
      jsonResponse({
        item: { ...bundle.project, id: destinationProject.id },
        replaced: true,
      }),
    );
    expect(
      await screen.findByText("Project APP imported successfully."),
    ).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(
      fetchMock.mock.calls.filter(([path]) => path === "/api/projects"),
    ).toHaveLength(2);
  });

  it("imports a new project key without a destructive warning", async () => {
    const newBundle: ProjectBundle = {
      ...bundle,
      project: { ...bundle.project, key: "NEW", title: "New Project" },
    };
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === "/api/projects/import" && init?.method === "POST") {
          return jsonResponse(
            { item: newBundle.project, replaced: false },
            201,
          );
        }
        return stateResponse(path) ?? jsonResponse({ error: "Not found" }, 404);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const AppShell = await loadAppShell();
    render(<AppShell />);
    await screen.findByText("Current Application");

    await userEvent.click(
      screen.getByRole("button", { name: "Import Project" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.upload(
      dialog.querySelector("input[type='file']") as HTMLInputElement,
      new File([JSON.stringify(newBundle)], "NEW-project.json", {
        type: "application/json",
      }),
    );
    expect(
      within(dialog).queryByText(/will replace all current project data/i),
    ).toBeNull();
    await userEvent.click(
      await within(dialog).findByRole("button", { name: "Import Project" }),
    );
    const importCall = fetchMock.mock.calls.find(
      ([path]) => path === "/api/projects/import",
    );
    expect(JSON.parse(String(importCall?.[1]?.body))).toMatchObject({
      replaceExisting: false,
    });
  });

  it("keeps the modal open when a selected file is invalid", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (input: RequestInfo | URL) =>
          stateResponse(String(input)) ?? jsonResponse({}, 404),
      ),
    );
    const AppShell = await loadAppShell();
    render(<AppShell />);
    await screen.findByText("Current Application");
    await userEvent.click(
      screen.getByRole("button", { name: "Import Project" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.upload(
      dialog.querySelector("input[type='file']") as HTMLInputElement,
      new File(["not JSON"], "broken.json", { type: "application/json" }),
    );

    expect(
      await within(dialog).findByText("Project file is not valid JSON"),
    ).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});
