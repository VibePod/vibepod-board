// @vitest-environment jsdom

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BoardCard, BoardColumns, Idea } from "../src/shared/types.js";

const timestamp = "2026-10-29T08:00:00.000Z";
const later = "2026-10-29T09:00:00.000Z";
const diverged =
  "The branch vp-1 on origin (aaaa) and in /work (bbbb) have diverged; not picking one. Bring them together, then plan the task again";
const question = "Commit 1234 would be lost; keep it?";

const project = {
  id: "project-1",
  key: "VP",
  title: "VibePod",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};

const idea = (patch: Partial<Idea>): Idea => ({
  id: "idea-1",
  projectId: project.id,
  taskNumber: 1,
  title: "Task",
  summary: "",
  details: "",
  status: "ready",
  labels: [],
  acceptanceCriteria: [],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const card = (patch: Partial<BoardCard>): BoardCard => ({
  id: "card-1",
  projectId: project.id,
  ideaId: "idea-1",
  title: "Task",
  details: "",
  column: "planned",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  attempts: 0,
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

type Cards = Record<string, BoardCard>;

const initialCards = (): Cards => ({
  "card-1": card({
    id: "card-1",
    ideaId: "idea-1",
    title: "Diverged branch",
    branchName: "vp-1",
    blockedAt: timestamp,
    blockedReason: diverged,
  }),
  "card-2": card({
    id: "card-2",
    ideaId: "idea-2",
    title: "Being rebased",
    column: "review",
    branchName: "vp-2",
    rebaseTarget: "default_branch",
    rebaseRequestedBy: "admin",
    rebaseRequestedAt: timestamp,
    rebaseHolder: "Claude::Rebaser",
    rebaseClaimedAt: timestamp,
    rebaseExpiresAt: "2999-01-01T00:00:00.000Z",
  }),
  "card-3": card({
    id: "card-3",
    ideaId: "idea-3",
    title: "Rebase question",
    column: "pr_ready",
    branchName: "vp-3",
    rebaseTarget: "both",
    rebaseRequestedBy: "admin",
    rebaseRequestedAt: timestamp,
    blockedAt: timestamp,
    blockedReason: `Needs input: ${question}`,
    question,
  }),
  "card-4": card({
    id: "card-4",
    ideaId: "idea-4",
    title: "No branch yet",
  }),
  "card-5": card({
    id: "card-5",
    ideaId: "idea-5",
    title: "Running now",
    column: "in_progress",
    branchName: "vp-5",
    assignee: "Claude::Runner",
    claimedAt: timestamp,
  }),
});

const boardApi = () => {
  const ideas = [1, 2, 3, 4, 5].map((n) =>
    idea({ id: `idea-${n}`, taskNumber: n, title: `Task ${n}` }),
  );
  const cards = initialCards();
  const columns = (): BoardColumns => {
    const result: BoardColumns = {
      ready: [],
      planned: [],
      in_progress: [],
      review: [],
      pr_ready: [],
      done: [],
    };
    for (const item of Object.values(cards)) result[item.column].push(item);
    return result;
  };
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    if (path === "/api/auth/me") {
      return jsonResponse({ authenticated: true, username: "admin" });
    }
    if (path === "/api/projects") return jsonResponse({ items: [project] });
    if (path === "/api/ideas") return jsonResponse({ items: ideas });
    if (path === "/api/documents") return jsonResponse({ items: [] });
    if (path === "/api/board") return jsonResponse({ columns: columns() });
    if (path === "/api/board/archived") return jsonResponse({ items: [] });
    if (path.endsWith("/history") || path.endsWith("/runs")) {
      return jsonResponse({ items: [] });
    }
    const rebase = path.match(/^\/api\/board\/(card-\d)\/rebase$/);
    if (rebase && method === "POST") {
      const current = cards[rebase[1]];
      if (body.expectedUpdatedAt !== current.updatedAt) {
        return jsonResponse({ error: "Board card changed" }, 409);
      }
      cards[rebase[1]] = {
        ...current,
        blockedAt: undefined,
        blockedReason: undefined,
        rebaseTarget: body.target,
        rebaseRequestedBy: "admin",
        rebaseRequestedAt: later,
        updatedAt: later,
      };
      return jsonResponse({ item: cards[rebase[1]] });
    }
    const cancel = path.match(/^\/api\/board\/(card-\d)\/rebase\/cancel$/);
    if (cancel && method === "POST") {
      cards[cancel[1]] = {
        ...cards[cancel[1]],
        rebaseTarget: undefined,
        rebaseRequestedBy: undefined,
        rebaseRequestedAt: undefined,
        rebaseHolder: undefined,
        rebaseClaimedAt: undefined,
        rebaseExpiresAt: undefined,
        updatedAt: later,
      };
      return jsonResponse({ item: cards[cancel[1]] });
    }
    const answer = path.match(/^\/api\/board\/(card-\d)\/answer$/);
    if (answer && method === "POST") {
      cards[answer[1]] = {
        ...cards[answer[1]],
        blockedAt: undefined,
        blockedReason: undefined,
        question: undefined,
        updatedAt: later,
      };
      return jsonResponse({ item: cards[answer[1]] });
    }
    return jsonResponse({ error: "Not found" }, 404);
  });
};

const posts = (fetchMock: ReturnType<typeof boardApi>) =>
  fetchMock.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "POST")
    .map(([input, init]) => {
      const body = (init as RequestInit).body;
      return [String(input), body ? JSON.parse(String(body)) : undefined];
    });

const renderBoard = async () => {
  window.history.replaceState(null, "", `/projects/${project.id}/board`);
  const fetchMock = boardApi();
  vi.stubGlobal("fetch", fetchMock);
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  await screen.findByText("Diverged branch");
  return fetchMock;
};

const cardFor = (title: string) =>
  screen.getByText(title).closest(".compact-card") as HTMLElement;

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
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("rebase requests on the board", () => {
  it("asks for a rebase of a diverged card, which then shows as pending", async () => {
    const fetchMock = await renderBoard();

    const blocked = cardFor("Diverged branch");
    expect(within(blocked).getByText("Blocked")).toBeTruthy();
    await userEvent.click(
      within(blocked).getByRole("button", { name: "Rebase Diverged branch" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Rebase: Diverged branch",
    });
    // Both is the default target.
    expect(
      (
        within(dialog).getByLabelText(
          "Origin's task branch, then the default branch",
        ) as HTMLInputElement
      ).checked,
    ).toBe(true);
    await userEvent.click(
      within(dialog).getByLabelText("Origin's task branch"),
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Request rebase" }),
    );

    expect(
      await screen.findByText(
        "Asked for a rebase of Diverged branch onto origin's task branch.",
      ),
    ).toBeTruthy();
    expect(posts(fetchMock)).toEqual([
      [
        "/api/board/card-1/rebase",
        { target: "origin", expectedUpdatedAt: timestamp },
      ],
    ]);
    await waitFor(() => {
      const pending = cardFor("Diverged branch");
      expect(within(pending).getByText("Rebase pending")).toBeTruthy();
      expect(within(pending).queryByText("Blocked")).toBeNull();
      expect(
        within(pending).getByText(
          "Rebase onto origin's task branch requested by admin; waiting for a rebase worker",
        ),
      ).toBeTruthy();
    });
  });

  it("shows the worker holding a rebase and cancels it", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = await renderBoard();

    const held = cardFor("Being rebased");
    expect(within(held).getByText("Rebasing")).toBeTruthy();
    expect(
      within(held).getByText(
        "Claude::Rebaser is rebasing onto the default branch",
      ),
    ).toBeTruthy();
    // A held rebase cannot be asked for again.
    expect(
      within(held).queryByRole("button", { name: "Rebase Being rebased" }),
    ).toBeNull();

    await userEvent.click(
      within(held).getByRole("button", {
        name: "Cancel rebase of Being rebased",
      }),
    );

    expect(
      await screen.findByText("Cancelled the rebase of Being rebased."),
    ).toBeTruthy();
    expect(window.confirm).toHaveBeenCalledOnce();
    expect(posts(fetchMock)).toEqual([
      ["/api/board/card-2/rebase/cancel", undefined],
    ]);
    await waitFor(() =>
      expect(
        within(cardFor("Being rebased")).queryByText("Rebasing"),
      ).toBeNull(),
    );
  });

  it("answers a rebase worker's question to resume the rebase", async () => {
    await renderBoard();

    const asking = cardFor("Rebase question");
    expect(within(asking).getByText("Needs input")).toBeTruthy();
    expect(within(asking).getByText(question)).toBeTruthy();
    await userEvent.click(
      within(asking).getByRole("button", { name: "Answer Rebase question" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Answer: Rebase question",
    });
    await userEvent.type(within(dialog).getByLabelText(/Answer/), "Keep it.");
    await userEvent.click(
      within(dialog).getByRole("button", {
        name: "Answer and resume the rebase",
      }),
    );

    expect(
      await screen.findByText(
        "Answered Rebase question; the rebase is pending again.",
      ),
    ).toBeTruthy();
    await waitFor(() =>
      expect(
        within(cardFor("Rebase question")).getByText("Rebase pending"),
      ).toBeTruthy(),
    );
  });

  it("offers Rebase only on cards with a branch that no run holds", async () => {
    await renderBoard();

    expect(
      within(cardFor("No branch yet")).queryByRole("button", {
        name: /^Rebase /,
      }),
    ).toBeNull();
    expect(
      within(cardFor("Running now")).queryByRole("button", {
        name: /^Rebase /,
      }),
    ).toBeNull();
  });
});
