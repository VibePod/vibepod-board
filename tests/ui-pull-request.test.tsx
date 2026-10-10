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

import {
  BranchPushBadge,
  PullRequestPanel,
  SuggestedPullRequest,
} from "../src/client/PullRequest.js";
import type { BoardCard, PushRequest } from "../src/shared/types.js";

const card = {
  id: "card-1",
  projectId: "project-1",
  ideaId: "idea-1",
  title: "Open PRs from the board",
  column: "pr_ready",
  branchName: "issue-33",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  attempts: 0,
  createdAt: "2026-09-30T08:00:00.000Z",
  updatedAt: "2026-09-30T08:00:00.000Z",
} as unknown as BoardCard;

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

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
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("opening a pull request", () => {
  it.each([
    { title: "Open PRs from the board", body: "Closes #33" },
    {
      title: "Add the Open PR dialog",
      body: "Adds the dialog.\n\nTested with Vitest.\n\nCloses #33",
    },
  ])("prefills and submits the draft: $title", async ({ title, body }) => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === "/api/board/card-1/pull-request" && !init?.method) {
          // The draft comes back as is, not wrapped in `item`.
          return jsonResponse({
            repository: "vibepod/vibepod-board",
            title,
            base: "main",
            bases: ["main"],
            body,
            draft: false,
          });
        }
        if (
          path === "/api/board/card-1/pull-request" &&
          init?.method === "POST"
        ) {
          return jsonResponse({ item: { ...card, githubPrNumber: 38 } });
        }
        return jsonResponse({ error: "Not found" }, 404);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const onChange = vi.fn(async () => undefined);

    render(
      <MantineProvider>
        <PullRequestPanel card={card} enabled onChange={onChange} />
      </MantineProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Open PR" }));

    const dialog = await screen.findByRole("dialog", {
      name: "Open Pull Request",
    });
    await waitFor(() =>
      expect(
        (
          dialog.querySelector(
            "input[type='text'], input:not([type])",
          ) as HTMLInputElement | null
        )?.value,
      ).toBe(title),
    );
    expect(screen.getByRole("textbox", { name: "Body" })).toHaveProperty(
      "value",
      body,
    );
    const submit = Array.from(
      dialog.querySelectorAll("button[type='submit']"),
    )[0] as HTMLButtonElement;
    expect(submit.disabled).toBe(false);
    await userEvent.click(submit);

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const post = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "POST",
    );
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      title,
      base: "main",
      body,
      draft: false,
    });
  });
});

describe("pushing a branch that is not on GitHub", () => {
  const draft = {
    repository: "vibepod/vibepod-board",
    title: "Push from the board",
    base: "main",
    bases: ["main"],
    body: "Closes #33",
    draft: false,
    branchOnGithub: false,
  };
  const pushRequest = (changes: Partial<PushRequest>): PushRequest => ({
    id: "push-1",
    cardId: "card-1",
    taskKey: "VP-33",
    branch: "issue-33",
    headSha: null,
    repositoryLocalPath: null,
    repositoryRemoteUrl: null,
    repository: "vibepod/vibepod-board",
    reason: "open_pr",
    status: "pending",
    assignee: null,
    leaseExpiresAt: null,
    pushedSha: null,
    error: null,
    pullRequestUrl: null,
    createdAt: "2026-10-09T08:00:00.000Z",
    updatedAt: "2026-10-09T08:00:00.000Z",
    ...changes,
  });

  const openDialog = async (liveWorkers: number, onChange = vi.fn()) => {
    render(
      <MantineProvider>
        <PullRequestPanel
          card={card}
          enabled
          liveWorkers={liveWorkers}
          onChange={onChange}
        />
      </MantineProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Open PR" }));
    return screen.findByRole("dialog", { name: "Open Pull Request" });
  };

  it("asks a worker to push, follows the request and reports the PR", async () => {
    const pullRequestUrl = "https://github.com/vibepod/vibepod-board/pull/40";
    let polled = 0;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === "/api/board/card-1/pull-request" && !init?.method) {
          return jsonResponse(draft);
        }
        if (path === "/api/board/card-1/push-request" && !init?.method) {
          polled += 1;
          // None asked for before the dialog opened; then the pushed request.
          return polled === 1
            ? jsonResponse({ error: "No push request for this card" }, 404)
            : jsonResponse(
                pushRequest({
                  status: "pushed",
                  assignee: "Claude::Worker",
                  pushedSha: "a".repeat(40),
                  pullRequestUrl,
                }),
              );
        }
        if (
          path === "/api/board/card-1/push-request" &&
          init?.method === "POST"
        ) {
          return jsonResponse(pushRequest({ headSha: "c".repeat(40) }));
        }
        return jsonResponse({ error: "Not found" }, 404);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const onChange = vi.fn(async () => undefined);

    const dialog = await openDialog(1, onChange);
    const push = await within(dialog).findByRole("button", {
      name: "Push and open PR",
    });
    await waitFor(() => expect(polled).toBe(1));
    await userEvent.click(push);

    expect(
      await within(dialog).findByText("Waiting for a worker"),
    ).toBeTruthy();
    // The push is of the handed-over commit, not whatever the branch points to.
    expect(within(dialog).getByText("c".repeat(12))).toBeTruthy();
    const post = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "POST",
    );
    expect(String(post?.[0])).toBe("/api/board/card-1/push-request");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      openPr: {
        title: "Push from the board",
        base: "main",
        body: "Closes #33",
        draft: false,
      },
    });
    expect(
      await within(dialog).findByText("Pull request opened", undefined, {
        timeout: 4000,
      }),
    ).toBeTruthy();
    expect(
      within(dialog).getByRole("link", { name: pullRequestUrl }),
    ).toBeTruthy();
    expect(onChange).toHaveBeenCalled();
  });

  it("shows the push command while no worker is online", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input) === "/api/board/card-1/pull-request"
          ? jsonResponse(draft)
          : jsonResponse({ error: "No push request for this card" }, 404),
      ),
    );

    const dialog = await openDialog(0);

    expect(
      await within(dialog).findByText("git push origin issue-33"),
    ).toBeTruthy();
    expect(
      within(dialog).getByRole("button", { name: "Copy push command" }),
    ).toBeTruthy();
    expect(
      within(dialog).queryByRole("button", { name: "Push and open PR" }),
    ).toBeNull();
    expect(
      within(dialog).getByRole("button", { name: "Open pull request" }),
    ).toBeTruthy();
  });

  it("tells PR ready cards on GitHub from local ones", () => {
    render(
      <MantineProvider>
        <BranchPushBadge card={{ headSha: "a".repeat(40) }} />
        <BranchPushBadge
          card={{ headSha: "a".repeat(40), branchPushedSha: "a".repeat(40) }}
        />
        <BranchPushBadge
          card={{ headSha: "b".repeat(40), branchPushedSha: "a".repeat(40) }}
        />
      </MantineProvider>,
    );

    expect(screen.getAllByText("local only")).toHaveLength(2);
    expect(screen.getAllByText("on GitHub")).toHaveLength(1);
  });
});

describe("the suggested pull request", () => {
  const suggested = {
    ...card,
    column: "review",
    suggestedPrTitle: "Open PRs from PR ready cards",
    suggestedPrBody: "Adds the **Open PR** dialog.",
    suggestedPrBy: "Claude::Runner::laptop",
    suggestedPrAt: "2026-09-30T08:00:00.000Z",
  } as BoardCard;

  it("shows the suggestion and saves an edit", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input) === "/api/board/card-1" && init?.method === "PATCH") {
          return jsonResponse({ item: suggested });
        }
        return jsonResponse({ error: "Not found" }, 404);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const onChange = vi.fn(async () => undefined);

    render(
      <MantineProvider>
        <SuggestedPullRequest card={suggested} editable onChange={onChange} />
      </MantineProvider>,
    );
    expect(screen.getByText("Open PRs from PR ready cards")).toBeTruthy();
    expect(screen.getByText("Open PR").tagName).toBe("STRONG");
    expect(
      screen.getByText(/Suggested by Claude::Runner::laptop/),
    ).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const title = screen.getByRole("textbox", { name: "Title" });
    await userEvent.clear(title);
    await userEvent.type(title, "Open pull requests");
    const description = screen.getByRole("textbox", { name: "Description" });
    await userEvent.clear(description);
    await userEvent.type(
      description,
      "Adds the **PR dialog**. Tested with Vitest.",
    );
    await userEvent.click(screen.getByRole("radio", { name: "Preview" }));
    expect(screen.getByText("PR dialog").tagName).toBe("STRONG");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const patch = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "PATCH",
    );
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
      prTitle: "Open pull requests",
      prBody: "Adds the **PR dialog**. Tested with Vitest.",
      expectedUpdatedAt: card.updatedAt,
    });
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("says when there is no suggestion yet", () => {
    render(
      <MantineProvider>
        <SuggestedPullRequest
          card={{ ...card, column: "review" } as BoardCard}
          editable={false}
          onChange={async () => undefined}
        />
      </MantineProvider>,
    );
    expect(screen.getByText(/No suggested description yet/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
  });
});
