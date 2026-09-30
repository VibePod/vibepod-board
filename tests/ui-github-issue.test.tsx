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
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  GitHubIssueBadge,
  GitHubSyncPanel,
} from "../src/client/GitHubIssue.js";
import type { Idea } from "../src/shared/types.js";

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
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const wrap = (node: ReactNode) =>
  render(<MantineProvider>{node}</MantineProvider>);

const stamp = "2026-09-27T12:00:00.000Z";

const idea = (patch: Partial<Idea> = {}): Idea => ({
  id: "idea-1",
  projectId: "project-1",
  taskNumber: 1,
  title: "Sync me",
  summary: "",
  details: "",
  status: "ready",
  labels: [],
  acceptanceCriteria: [],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  createdAt: stamp,
  updatedAt: stamp,
  ...patch,
});

const linked = {
  githubIssueUrl: "https://github.com/vibepod/board/issues/12",
  githubIssueNumber: 12,
  githubRepository: "vibepod/board",
  githubIssueState: "open" as const,
};

describe("GitHub issue badge", () => {
  it("opens the issue in a new tab without opening the card", async () => {
    const onCardClick = vi.fn();
    wrap(
      // biome-ignore lint/a11y/useKeyWithClickEvents: stands in for a clickable card
      // biome-ignore lint/a11y/noStaticElementInteractions: stands in for a clickable card
      <div onClick={onCardClick}>
        <GitHubIssueBadge
          link={{
            url: linked.githubIssueUrl,
            number: 12,
            repository: "vibepod/board",
            state: "closed",
          }}
        />
      </div>,
    );

    const link = screen.getByRole("link", {
      name: "Open GitHub issue vibepod/board#12 in a new tab",
    });
    expect(link.getAttribute("href")).toBe(linked.githubIssueUrl);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.textContent).toContain("#12");

    link.addEventListener("click", (event) => event.preventDefault());
    await userEvent.click(link);
    expect(onCardClick).not.toHaveBeenCalled();
  });
});

describe("GitHub sync panel", () => {
  it("disables both actions while GitHub sync is not configured", () => {
    wrap(
      <GitHubSyncPanel
        idea={idea(linked)}
        status={{ enabled: false }}
        onSync={vi.fn()}
      />,
    );

    expect(
      (
        screen.getByRole("button", {
          name: "Push to GitHub",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(
      (
        screen.getByRole("button", {
          name: "Pull from GitHub",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it("offers to create an issue for an unlinked task and cannot pull yet", async () => {
    const onSync = vi.fn(async () => undefined);
    wrap(
      <GitHubSyncPanel
        idea={idea()}
        status={{ enabled: true }}
        onSync={onSync}
      />,
    );

    expect(
      (
        screen.getByRole("button", {
          name: "Pull from GitHub",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(screen.queryByRole("link")).toBeNull();

    await userEvent.click(
      screen.getByRole("button", { name: "Create GitHub issue" }),
    );
    expect(onSync).toHaveBeenCalledWith("push");
  });

  it("shows the sync time and offers a pull when a push was stale", async () => {
    const onSync = vi.fn(async (action: "push" | "pull") => {
      if (action === "push") {
        throw new Error(
          "Issue changed on GitHub since the last sync — pull first",
        );
      }
    });
    wrap(
      <GitHubSyncPanel
        idea={idea({ ...linked, githubSyncedAt: new Date().toISOString() })}
        status={{ enabled: true }}
        onSync={onSync}
      />,
    );
    expect(screen.getByText("Synced just now")).toBeTruthy();

    await userEvent.click(
      screen.getByRole("button", { name: "Push to GitHub" }),
    );
    expect(
      await screen.findByText(
        "Issue changed on GitHub since the last sync — pull first",
      ),
    ).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Pull now" }));
    await waitFor(() => expect(onSync).toHaveBeenLastCalledWith("pull"));
    await waitFor(() => expect(screen.queryByText(/pull first/)).toBeNull());
  });
});

describe("task view after a GitHub pull", () => {
  it("shows the refreshed board card, not the one it was opened with", async () => {
    window.history.replaceState(null, "", "/projects/project-1/board");
    class ResizeObserverStub {
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();
    }
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    const project = {
      id: "project-1",
      key: "APP",
      title: "App",
      summary: "",
      createdAt: stamp,
      updatedAt: stamp,
    };
    let details = "Old card details";
    const card = () => ({
      id: "card-1",
      projectId: "project-1",
      ideaId: "idea-1",
      title: "Sync me",
      details,
      column: "ready",
      labels: [],
      dependsOn: [],
      blockedBy: [],
      githubIssueUrl: linked.githubIssueUrl,
      githubIssueNumber: 12,
      createdAt: stamp,
      updatedAt: stamp,
    });
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), {
        headers: { "Content-Type": "application/json" },
      });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (
          path === "/api/ideas/idea-1/github/pull" &&
          init?.method === "POST"
        ) {
          details = "Body pulled from GitHub";
          return json({ item: idea({ ...linked, details }) });
        }
        if (path === "/api/auth/me") {
          return json({ authenticated: true, username: "admin" });
        }
        if (path === "/api/projects") return json({ items: [project] });
        if (path === "/api/ideas") {
          return json({ items: [idea({ ...linked, details })] });
        }
        if (path === "/api/documents") return json({ items: [] });
        if (path === "/api/github") return json({ enabled: true });
        if (path === "/api/board") {
          return json({
            columns: {
              ready: [card()],
              planned: [],
              in_progress: [],
              review: [],
              pr_ready: [],
              done: [],
            },
          });
        }
        return new Response("{}", { status: 404 });
      }),
    );
    const { AppShell } = await import("../src/client/main.js");
    render(<AppShell />);

    await userEvent.click(await screen.findByText("Sync me"));
    const view = await screen.findByRole("dialog");
    expect(within(view).getByText("Old card details")).toBeTruthy();
    await userEvent.click(
      within(view).getByRole("button", { name: "Pull from GitHub" }),
    );

    expect(
      await within(view).findByText("Body pulled from GitHub"),
    ).toBeTruthy();
  });
});
