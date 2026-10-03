// @vitest-environment jsdom
import { MantineProvider } from "@mantine/core";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TaskReviews } from "../src/client/TaskReviews.js";
import { TaskHistory } from "../src/client/TaskHistory.js";
import { TaskRuns } from "../src/client/TaskRuns.js";
import { WorkersList } from "../src/client/Workers.js";
import { ApproveDialog } from "../src/client/ConversationDialogs.js";
import type { BoardCard } from "../src/shared/types.js";
const timestamp = "2026-09-30T08:00:00Z";
const response = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
const reviewState = {
  taskId: "task",
  headSha: "abcdef123456",
  approvals: 1,
  requiredApprovals: 2,
  reviewRounds: 2,
  maxReviewRounds: 3,
  items: [
    {
      id: "live",
      reviewer: "Alice",
      headSha: "abcdef123456",
      open: true,
      createdAt: timestamp,
    },
    {
      id: "old",
      reviewer: "Bob",
      headSha: "123456abcdef",
      open: false,
      verdict: "rework",
      feedback: "Fix the race",
      createdAt: timestamp,
      endedAt: timestamp,
    },
    {
      id: "approved",
      reviewer: "Carol",
      headSha: "abcdef123456",
      open: false,
      verdict: "approve",
      feedback: "Looks good",
      createdAt: timestamp,
    },
  ],
};
beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
const mount = (ui: React.ReactNode) =>
  render(<MantineProvider>{ui}</MantineProvider>);
it("shows current approvals and cancels one open reviewer without moving the card", async () => {
  let cancelled = false;
  const fetchMock = vi.fn(async (_path: unknown, init?: RequestInit) => {
    if (init?.method === "POST") {
      cancelled = true;
      return response({});
    }
    return response({
      ...reviewState,
      items: reviewState.items.map((r) =>
        r.id === "live" && cancelled ? { ...r, open: false } : r,
      ),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  const changed = vi.fn(async () => {});
  mount(
    <TaskReviews cardId="card" reloadKey="1" compact onChanged={changed} />,
  );
  await screen.findByText("1/2 approvals");
  expect(screen.getByText("Review rounds: 2/3")).toBeTruthy();
  expect(screen.queryByText("Bob")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Cancel review by Alice" }),
  );
  await waitFor(() => expect(screen.queryByText("Alice")).toBeNull());
  expect(
    fetchMock.mock.calls.some(
      ([path, init]) =>
        path === "/api/board/card/reviews/live/cancel" &&
        init?.method === "POST",
    ),
  ).toBe(true);
  expect(changed).toHaveBeenCalledOnce();
});
it("lists every review, short commit, feedback and outdated verdicts", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response(reviewState)),
  );
  mount(<TaskReviews cardId="card" reloadKey="1" />);
  await screen.findByText("Bob");
  expect(screen.getByText("123456ab")).toBeTruthy();
  expect(screen.getByText("Outdated")).toBeTruthy();
  expect(screen.getByText("Fix the race")).toBeTruthy();
  expect(screen.getByText("Approved")).toBeTruthy();
  expect(
    screen.getAllByText(new Date(timestamp).toLocaleString()),
  ).toHaveLength(3);
});
it("keeps cancellation errors visible", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_path: unknown, init?: RequestInit) =>
      init?.method === "POST"
        ? response({ error: "Review already ended" }, 409)
        : response(reviewState),
    ),
  );
  mount(<TaskReviews cardId="card" reloadKey="1" compact />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Cancel review by Alice" }),
  );
  await screen.findByText(/Review already ended/);
});
it("shows worker mode and reviewer activity", () => {
  mount(
    <WorkersList
      workers={[
        {
          id: "w",
          projectId: "p",
          name: "Alice",
          agent: "codex",
          machine: "laptop",
          mode: "review",
          status: "working",
          taskId: "t",
          taskKey: "VP-12",
          step: "handing_over",
          startedAt: timestamp,
          lastSeenAt: timestamp,
        },
      ]}
      onOpenTask={vi.fn()}
    />,
  );
  expect(screen.getByText("review")).toBeTruthy();
  expect(screen.getByText("reviewing VP-12")).toBeTruthy();
  expect(screen.getByText("Submitting verdict")).toBeTruthy();
});
it("allows a manual approval without a note", async () => {
  const submit = vi.fn(async () => {});
  mount(
    <ApproveDialog
      card={{ id: "card" } as BoardCard}
      onClose={vi.fn()}
      onSubmit={submit}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
  await waitFor(() => expect(submit).toHaveBeenCalledWith({ id: "card" }, ""));
});
it.each([
  ["approve", "Approved"],
  ["rework", "Rework"],
  ["needs_input", "Needs input"],
  ["failed", "Failed"],
])(
  "shows review run %s with feedback and verify output alongside implementation",
  async (verdict, label) => {
    const base = {
      ideaId: "task",
      summary: "",
      commits: [],
      verifyOutputTruncated: false,
      createdAt: timestamp,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        response({
          items: [
            {
              ...base,
              id: "r",
              kind: "review",
              verdict,
              outcome: verdict === "failed" ? "failed" : "done",
              feedback: "Review feedback",
              verifyOutput: "Tests passed",
              verifyExitCode: 0,
            },
            { ...base, id: "i", kind: "implement", outcome: "done" },
          ],
        }),
      ),
    );
    mount(<TaskRuns ideaId="task" />);
    await screen.findByText(label);
    expect(screen.getByText("review")).toBeTruthy();
    expect(screen.getByText("implement")).toBeTruthy();
    expect(screen.getByText("Review feedback")).toBeTruthy();
    expect(screen.getByText("Tests passed")).toBeTruthy();
  },
);

it("shows approvals and rework in history with reviewer actors and the round count", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      response({
        items: [
          {
            id: "approved",
            kind: "approved",
            actor: "Alice",
            message: "Approved current head",
            createdAt: timestamp,
          },
          {
            id: "rework",
            kind: "rework_requested",
            actor: "Bob",
            message: "Round 1 needs rework",
            createdAt: timestamp,
          },
        ],
      }),
    ),
  );
  mount(<TaskHistory ideaId="task" reviewRounds={1} maxReviewRounds={3} />);
  await screen.findByText("Approved");
  expect(screen.getByText("Rework requested")).toBeTruthy();
  expect(screen.getByText(/Alice ·/)).toBeTruthy();
  expect(screen.getByText(/Bob ·/)).toBeTruthy();
  expect(screen.getByText("Review rounds: 1/3")).toBeTruthy();
});
