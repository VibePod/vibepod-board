// @vitest-environment jsdom

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BoardCard, BoardColumns, Idea } from "../src/shared/types.js";

const timestamp = "2026-10-01T08:00:00.000Z";
const conflict =
  "Board card changed since it was read: updatedAt is now 2026-10-01T09:00:00+00:00";

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
  column: "ready",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

type BoardOptions = {
  stale?: boolean;
  /** Fails every board reload once a card was written. */
  reloadFails?: boolean;
  /** Holds every card write until it resolves. */
  writesWaitFor?: Promise<void>;
};

/** A board with a free Ready task and one that waits for two unfinished tasks. */
const boardApi = ({
  stale = false,
  reloadFails = false,
  writesWaitFor,
}: BoardOptions = {}) => {
  let written = false;
  const ideas = [
    idea({ id: "idea-1", taskNumber: 1, title: "Free work" }),
    idea({ id: "idea-2", taskNumber: 2, title: "First dependency" }),
    idea({ id: "idea-3", taskNumber: 3, title: "Second dependency" }),
    idea({
      id: "idea-4",
      taskNumber: 4,
      title: "Waiting work",
      dependsOn: ["idea-2", "idea-3"],
      blockedBy: ["idea-3", "idea-2"],
    }),
  ];
  const cards = [
    card({ id: "card-1", ideaId: "idea-1", title: "Free work" }),
    card({
      id: "card-4",
      ideaId: "idea-4",
      title: "Waiting work",
      dependsOn: ["idea-2", "idea-3"],
      blockedBy: ["idea-3", "idea-2"],
    }),
  ];
  const columns = (): BoardColumns => ({
    ready: cards.filter((item) => item.column === "ready"),
    planned: cards.filter((item) => item.column === "planned"),
    in_progress: [],
    review: [],
    pr_ready: [],
    done: [],
  });
  /** Deletes a card elsewhere: later loads and writes no longer find it. */
  const remove = (id: string) => {
    cards.splice(
      cards.findIndex((item) => item.id === id),
      1,
    );
  };
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path === "/api/auth/me") {
        return jsonResponse({ authenticated: true, username: "admin" });
      }
      if (path === "/api/projects") return jsonResponse({ items: [project] });
      if (path === "/api/ideas") return jsonResponse({ items: ideas });
      if (path === "/api/documents") return jsonResponse({ items: [] });
      if (path === "/api/board") {
        if (reloadFails && written) {
          return jsonResponse({ error: "Board unavailable" }, 500);
        }
        return jsonResponse({ columns: columns() });
      }
      if (path === "/api/board/archived") return jsonResponse({ items: [] });
      if (path.endsWith("/history") || path.includes("/runs")) {
        return jsonResponse({ items: [] });
      }
      const target = cards.find((item) => path === `/api/board/${item.id}`);
      if (target && method === "PATCH") {
        await writesWaitFor;
        if (stale) return jsonResponse({ error: conflict }, 409);
        const body = JSON.parse(String(init?.body));
        target.column = body.column;
        target.updatedAt = "2026-10-01T09:00:00.000Z";
        written = true;
        return jsonResponse({ item: target });
      }
      if (path === "/api/board/batch" && method === "POST") {
        if (stale) {
          return jsonResponse(
            { error: `Batch item 1 (card-1) failed: ${conflict}` },
            409,
          );
        }
        const body = JSON.parse(String(init?.body)) as {
          items: { id: string; column: BoardCard["column"] }[];
        };
        const missing = body.items.findIndex(
          (item) => !cards.some((each) => each.id === item.id),
        );
        if (missing >= 0) {
          const id = body.items[missing]?.id;
          return jsonResponse(
            {
              error: `Batch item ${missing + 1} (${id}) failed: Board card not found: ${id}`,
            },
            404,
          );
        }
        const planned = body.items.flatMap((item) => {
          const target = cards.find((each) => each.id === item.id);
          if (!target) return [];
          target.column = item.column;
          return [target];
        });
        written = true;
        return jsonResponse({ items: planned });
      }
      return jsonResponse({ error: "Not found" }, 404);
    },
  );
  /**
   * Finishes a task elsewhere: no card waits for it any more, though the
   * waiting cards keep their updatedAt.
   */
  const finish = (ideaId: string) => {
    const finished = ideas.find((item) => item.id === ideaId);
    if (finished) finished.status = "done";
    for (const item of [...ideas, ...cards]) {
      item.blockedBy = item.blockedBy.filter((id) => id !== ideaId);
    }
  };
  return Object.assign(fetchMock, { remove, finish });
};

const renderBoard = async (options?: BoardOptions) => {
  window.history.replaceState(null, "", `/projects/${project.id}/board`);
  const fetchMock = boardApi(options);
  vi.stubGlobal("fetch", fetchMock);
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  await screen.findByText("Free work");
  return fetchMock;
};

const patchesOf = (fetchMock: ReturnType<typeof boardApi>) =>
  fetchMock.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "PATCH")
    .map(([input, init]) => [
      String(input),
      JSON.parse(String((init as RequestInit).body)),
    ]);

const cardFor = (title: string) =>
  screen.getByText(title).closest(".compact-card") as HTMLElement;

const columnOf = (element: HTMLElement) =>
  element.closest(".column")?.querySelector(".column-title")?.textContent;

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

describe("planning a card from Ready", () => {
  it("plans a Ready card with its button, guarded by the card's updatedAt", async () => {
    const fetchMock = await renderBoard();

    await userEvent.click(
      within(cardFor("Free work")).getByRole("button", {
        name: "Plan Free work",
      }),
    );

    expect(
      await screen.findByText("Planned VP-1: a worker will pick it up."),
    ).toBeTruthy();
    expect(patchesOf(fetchMock)).toEqual([
      [
        "/api/board/card-1",
        { column: "planned", expectedUpdatedAt: timestamp },
      ],
    ]);
    await waitFor(() => expect(columnOf(cardFor("Free work"))).toBe("Planned"));
    expect(
      within(cardFor("Free work")).queryByRole("button", { name: /^Plan / }),
    ).toBeNull();
    // Planning does not open the task view.
    expect(screen.queryByRole("dialog", { name: "Task Overview" })).toBeNull();
  });

  it("says which unfinished dependencies a planned task waits for", async () => {
    await renderBoard();

    await userEvent.click(
      within(cardFor("Waiting work")).getByRole("button", {
        name: "Plan Waiting work",
      }),
    );

    expect(
      await screen.findByText("Planned VP-4; it waits for VP-2 and VP-3."),
    ).toBeTruthy();
  });

  it("names the dependencies the card waits for when it was planned", async () => {
    const fetchMock = await renderBoard();
    fetchMock.finish("idea-3");

    await userEvent.click(
      within(cardFor("Waiting work")).getByRole("button", {
        name: "Plan Waiting work",
      }),
    );

    expect(
      await screen.findByText("Planned VP-4; it waits for VP-2."),
    ).toBeTruthy();
  });

  it("shows the conflict and keeps the card when the view is stale", async () => {
    await renderBoard({ stale: true });

    await userEvent.click(
      within(cardFor("Free work")).getByRole("button", {
        name: "Plan Free work",
      }),
    );

    expect(
      await screen.findByText(
        `${conflict}. The board was reloaded; check the card again.`,
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/^Planned VP-1/)).toBeNull();
    expect(columnOf(cardFor("Free work"))).toBe("Ready");
  });

  it("keeps a successful plan when the reload fails", async () => {
    await renderBoard({ reloadFails: true });

    await userEvent.click(
      within(cardFor("Free work")).getByRole("button", {
        name: "Plan Free work",
      }),
    );

    expect(
      await screen.findByText("Planned VP-1: a worker will pick it up."),
    ).toBeTruthy();
    expect(await screen.findByText("Board unavailable")).toBeTruthy();
    expect(screen.queryByText(/check the card again/)).toBeNull();
  });

  it("sends one request while a plan is in flight", async () => {
    let finishWrite = () => {};
    const fetchMock = await renderBoard({
      writesWaitFor: new Promise((resolve) => {
        finishWrite = resolve;
      }),
    });
    const plan = within(cardFor("Free work")).getByRole("button", {
      name: "Plan Free work",
    });

    await userEvent.click(plan);
    await waitFor(() => expect(plan.hasAttribute("disabled")).toBe(true));
    plan.click();
    finishWrite();

    expect(
      await screen.findByText("Planned VP-1: a worker will pick it up."),
    ).toBeTruthy();
    expect(patchesOf(fetchMock)).toHaveLength(1);
    expect(screen.queryByText(/check the card again/)).toBeNull();
  });

  it("plans with the keyboard without opening the task view", async () => {
    const fetchMock = await renderBoard();

    const plan = within(cardFor("Free work")).getByRole("button", {
      name: "Plan Free work",
    });
    plan.focus();
    expect(document.activeElement).toBe(plan);
    await userEvent.keyboard("{Enter}");

    expect(
      await screen.findByText("Planned VP-1: a worker will pick it up."),
    ).toBeTruthy();
    expect(patchesOf(fetchMock)).toHaveLength(1);
    expect(screen.queryByRole("dialog", { name: "Task Overview" })).toBeNull();
  });

  it("reaches the Plan button by tabbing from its card", async () => {
    await renderBoard();

    cardFor("Free work").focus();
    await userEvent.tab();

    expect(document.activeElement).toBe(
      within(cardFor("Free work")).getByRole("button", {
        name: "Plan Free work",
      }),
    );
  });

  it("plans from the task view while the card is in Ready", async () => {
    const fetchMock = await renderBoard();

    await userEvent.click(screen.getByText("Waiting work"));
    const view = await screen.findByRole("dialog", { name: "Task Overview" });
    await userEvent.click(
      within(view).getByRole("button", { name: "Plan Waiting work" }),
    );

    expect(
      await screen.findByText("Planned VP-4; it waits for VP-2 and VP-3."),
    ).toBeTruthy();
    expect(patchesOf(fetchMock)).toEqual([
      [
        "/api/board/card-4",
        { column: "planned", expectedUpdatedAt: timestamp },
      ],
    ]);
    await waitFor(() =>
      expect(columnOf(cardFor("Waiting work"))).toBe("Planned"),
    );

    // Once planned, the task view no longer offers the action.
    await userEvent.click(screen.getByText("Waiting work"));
    const planned = await screen.findByRole("dialog", {
      name: "Task Overview",
    });
    expect(
      within(planned).queryByRole("button", { name: "Plan Waiting work" }),
    ).toBeNull();
  });

  it("shows a stale conflict from the task view on the board", async () => {
    await renderBoard({ stale: true });

    await userEvent.click(screen.getByText("Free work"));
    const view = await screen.findByRole("dialog", { name: "Task Overview" });
    await userEvent.click(
      within(view).getByRole("button", { name: "Plan Free work" }),
    );

    expect(
      await screen.findByText(
        `${conflict}. The board was reloaded; check the card again.`,
      ),
    ).toBeTruthy();
  });
});

describe("planning every Ready card", () => {
  it("plans the Ready column in one guarded batch after confirming", async () => {
    const fetchMock = await renderBoard();

    await userEvent.click(
      screen.getByRole("button", { name: "Plan all ready tasks" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Plan Ready Tasks",
    });
    expect(within(dialog).getByText("2 ready tasks")).toBeTruthy();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Plan Tasks" }),
    );

    expect(
      await screen.findByText(
        "Planned 2 ready tasks; VP-4 waits for its dependencies.",
      ),
    ).toBeTruthy();
    const batches = fetchMock.mock.calls
      .filter(([input]) => String(input) === "/api/board/batch")
      .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
    expect(batches).toEqual([
      {
        items: [
          { id: "card-1", column: "planned", expectedUpdatedAt: timestamp },
          { id: "card-4", column: "planned", expectedUpdatedAt: timestamp },
        ],
      },
    ]);
    await waitFor(() => expect(columnOf(cardFor("Free work"))).toBe("Planned"));
    expect(columnOf(cardFor("Waiting work"))).toBe("Planned");
    expect(
      screen.queryByRole("button", { name: "Plan all ready tasks" }),
    ).toBeNull();
  });

  it("names the tasks that still wait once the batch is written", async () => {
    const fetchMock = await renderBoard();

    await userEvent.click(
      screen.getByRole("button", { name: "Plan all ready tasks" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Plan Ready Tasks",
    });
    fetchMock.finish("idea-2");
    fetchMock.finish("idea-3");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Plan Tasks" }),
    );

    expect(
      await screen.findByText(
        "Planned 2 ready tasks: workers will pick them up.",
      ),
    ).toBeTruthy();
  });

  it("plans nothing when the confirmation is cancelled", async () => {
    const fetchMock = await renderBoard();

    await userEvent.click(
      screen.getByRole("button", { name: "Plan all ready tasks" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Plan Ready Tasks",
    });
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cancel" }),
    );

    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Plan Ready Tasks" }),
      ).toBeNull(),
    );
    expect(
      fetchMock.mock.calls.some(
        ([input]) => String(input) === "/api/board/batch",
      ),
    ).toBe(false);
  });

  it("closes and refreshes when a listed card was removed meanwhile", async () => {
    const fetchMock = await renderBoard();

    await userEvent.click(
      screen.getByRole("button", { name: "Plan all ready tasks" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Plan Ready Tasks",
    });
    fetchMock.remove("card-4");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Plan Tasks" }),
    );

    expect(
      await screen.findByText(
        "Batch item 2 (card-4) failed: Board card not found: card-4. The board was reloaded; check the cards again.",
      ),
    ).toBeTruthy();
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Plan Ready Tasks" }),
      ).toBeNull(),
    );
    expect(screen.queryByText("Waiting work")).toBeNull();
    expect(columnOf(cardFor("Free work"))).toBe("Ready");
  });

  it("refuses the whole batch when the view is stale", async () => {
    await renderBoard({ stale: true });

    await userEvent.click(
      screen.getByRole("button", { name: "Plan all ready tasks" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Plan Ready Tasks",
    });
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Plan Tasks" }),
    );

    expect(
      await screen.findByText(
        `Batch item 1 (card-1) failed: ${conflict}. The board was reloaded; check the cards again.`,
      ),
    ).toBeTruthy();
    expect(columnOf(cardFor("Free work"))).toBe("Ready");
  });
});
