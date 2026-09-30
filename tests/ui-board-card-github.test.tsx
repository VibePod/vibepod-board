// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BoardCard, Idea } from "../src/shared/types.js";

const timestamp = "2026-09-30T12:00:00.000Z";
const project = {
  id: "project-1",
  key: "VP",
  title: "Current Application",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};
const localPath = "/root/DEV/VibePod/dev-workspace/public/vibepod-board";
const githubRemote = "git@github.com:VibePod/vibepod-board.git";
const gitlabRemote = "https://gitlab.com/vibepod/vibepod-board.git";

const idea = (
  patch: Partial<Idea> & Pick<Idea, "id" | "taskNumber" | "title">,
): Idea => ({
  projectId: project.id,
  summary: "",
  details: "",
  status: "ready",
  labels: [],
  acceptanceCriteria: [],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  repositoryLocalPath: localPath,
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const ideas = [
  idea({
    id: "idea-1",
    taskNumber: 1,
    title: "Issue task",
    repositoryRemoteUrl: githubRemote,
    githubIssueUrl: "https://github.com/VibePod/vibepod-board/issues/23",
    githubIssueNumber: 23,
    githubRepository: "VibePod/vibepod-board",
    githubIssueState: "open",
  }),
  idea({
    id: "idea-2",
    taskNumber: 2,
    title: "Repository task",
    repositoryRemoteUrl: githubRemote,
  }),
  idea({
    id: "idea-3",
    taskNumber: 3,
    title: "GitLab task",
    repositoryRemoteUrl: gitlabRemote,
  }),
  idea({
    id: "idea-4",
    taskNumber: 4,
    title: "PR ready task",
    repositoryRemoteUrl: githubRemote,
    githubIssueUrl: "https://github.com/VibePod/vibepod-board/issues/24",
    githubIssueNumber: 24,
    githubRepository: "VibePod/vibepod-board",
    githubIssueState: "open",
  }),
  idea({
    id: "idea-5",
    taskNumber: 5,
    title: "Linked PR task",
    repositoryRemoteUrl: githubRemote,
  }),
  idea({
    id: "idea-6",
    taskNumber: 6,
    title: "Cleared remote task",
    repositoryRemoteUrl: githubRemote,
  }),
];
const cardPath = "/srv/card-checkout";

const pullRequest = (number: number): Partial<BoardCard> => ({
  githubPrUrl: `https://github.com/VibePod/vibepod-board/pull/${number}`,
  githubPrNumber: number,
  githubPrRepository: "VibePod/vibepod-board",
  githubPrState: "open",
});

const card = (source: Idea, patch: Partial<BoardCard> = {}): BoardCard => ({
  id: `card-${source.taskNumber}`,
  projectId: project.id,
  ideaId: source.id,
  title: source.title,
  details: "",
  column: "ready",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  repositoryLocalPath: source.repositoryLocalPath,
  repositoryRemoteUrl: source.repositoryRemoteUrl,
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

beforeEach(() => {
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
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/auth/me") {
        return jsonResponse({ authenticated: true, username: "admin" });
      }
      if (path === "/api/projects") return jsonResponse({ items: [project] });
      if (path === "/api/ideas") return jsonResponse({ items: ideas });
      if (path === "/api/documents") return jsonResponse({ items: [] });
      if (path === "/api/github") return jsonResponse({ enabled: false });
      if (path === "/api/board") {
        return jsonResponse({
          columns: {
            ready: [
              ...ideas.slice(0, 3).map((source) => card(source)),
              card(ideas[4], pullRequest(29)),
              // The card's remote was cleared, and its path set, on the card alone.
              card(ideas[5], {
                repositoryRemoteUrl: undefined,
                repositoryLocalPath: cardPath,
              }),
            ],
            planned: [],
            in_progress: [],
            review: [],
            pr_ready: [
              card(ideas[3], { column: "pr_ready", ...pullRequest(28) }),
            ],
            done: [],
          },
        });
      }
      return jsonResponse({ items: [] });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

const renderBoard = async () => {
  window.history.replaceState(null, "", `/projects/${project.id}/board`);
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  await screen.findByText("Issue task");
};

const boardCard = (title: string) =>
  screen
    .getAllByRole("button")
    .find(
      (element) =>
        element.classList.contains("compact-card") &&
        within(element).queryByRole("heading", { name: title }),
    ) as HTMLElement;

describe("board card GitHub link", () => {
  it("shows one GitHub link and no repository path or remote", async () => {
    await renderBoard();

    expect(screen.queryByText(localPath)).toBeNull();
    expect(screen.queryByText(githubRemote)).toBeNull();
    expect(screen.queryByText(gitlabRemote)).toBeNull();

    const issueLinks = within(boardCard("Issue task")).getAllByRole("link");
    expect(issueLinks).toHaveLength(1);
    expect(issueLinks[0].getAttribute("href")).toBe(
      "https://github.com/VibePod/vibepod-board/issues/23",
    );
    expect(issueLinks[0].textContent).toBe("#23");

    const repositoryLinks = within(boardCard("Repository task")).getAllByRole(
      "link",
    );
    expect(repositoryLinks).toHaveLength(1);
    expect(repositoryLinks[0].getAttribute("href")).toBe(
      "https://github.com/VibePod/vibepod-board",
    );
    expect(repositoryLinks[0].textContent).toBe("VibePod/vibepod-board");

    expect(within(boardCard("GitLab task")).queryAllByRole("link")).toEqual([]);
  });

  it("links the pull request in PR ready ahead of the issue", async () => {
    await renderBoard();

    const links = within(boardCard("PR ready task")).getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe(
      "https://github.com/VibePod/vibepod-board/pull/28",
    );
  });

  it("links the repository, not an earlier pull request, outside PR ready", async () => {
    await renderBoard();

    const links = within(boardCard("Linked PR task")).getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe(
      "https://github.com/VibePod/vibepod-board",
    );
  });

  it("follows the card's own repository fields", async () => {
    await renderBoard();

    // The remote was cleared on the card: no link, although the task has one.
    expect(
      within(boardCard("Cleared remote task")).queryAllByRole("link"),
    ).toEqual([]);

    fireEvent.click(boardCard("Cleared remote task"));
    const view = await screen.findByRole("dialog");
    expect(within(view).getByText(cardPath)).toBeTruthy();
    expect(within(view).queryByText(localPath)).toBeNull();
    expect(within(view).queryByText(githubRemote)).toBeNull();
  });

  it("opens GitHub without opening the task", async () => {
    await renderBoard();

    fireEvent.click(
      within(boardCard("Repository task")).getByRole("link", {
        name: "Open GitHub repository VibePod/vibepod-board in a new tab",
      }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps the path and remote in the task view", async () => {
    await renderBoard();

    fireEvent.click(boardCard("GitLab task"));
    const view = await screen.findByRole("dialog");
    expect(within(view).getByText(localPath)).toBeTruthy();
    expect(within(view).getByText(gitlabRemote)).toBeTruthy();
    expect(
      within(view).getByRole("button", { name: "Copy local path" }),
    ).toBeTruthy();
  });
});
