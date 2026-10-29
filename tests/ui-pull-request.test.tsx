// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  LinkedPullRequest,
  PullRequestPanel,
  SuggestedPullRequest,
  shouldSyncOnOpen,
} from "../src/client/PullRequest.js";
import type { BoardCard } from "../src/shared/types.js";

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

  it("saves against the version the edit started from", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ error: "Card changed" }, 409),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onChange = vi.fn(async () => undefined);

    const { rerender } = render(
      <MantineProvider>
        <SuggestedPullRequest card={suggested} editable onChange={onChange} />
      </MantineProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    // A background reload brings in a concurrently edited version.
    rerender(
      <MantineProvider>
        <SuggestedPullRequest
          card={
            {
              ...suggested,
              suggestedPrTitle: "Edited elsewhere",
              updatedAt: "2026-09-30T09:00:00.000Z",
            } as BoardCard
          }
          editable
          onChange={onChange}
        />
      </MantineProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByText("Card changed")).toBeTruthy());
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(String(init.body)).expectedUpdatedAt).toBe(
      card.updatedAt,
    );
    expect(onChange).not.toHaveBeenCalled();
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

describe("syncing a linked pull request", () => {
  const linked = {
    ...card,
    githubPrUrl: "https://github.com/vibepod/vibepod-board/pull/38",
    githubPrNumber: 38,
    githubPrRepository: "vibepod/vibepod-board",
    githubPrState: "open",
    githubPrSyncedAt: "2026-10-29T08:00:00.000Z",
  } as BoardCard;

  it("syncs from the Sync PR button", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ item: linked }));
    vi.stubGlobal("fetch", fetchMock);
    const onChange = vi.fn(async () => undefined);

    render(
      <MantineProvider>
        <PullRequestPanel card={linked} enabled onChange={onChange} />
      </MantineProvider>,
    );
    expect(screen.getByText(/^Synced /)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Sync PR" }));

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/board/card-1/pull-request/sync",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows why a sync failed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ error: "GitHub rate limit reached; retry in 60s" }, 502),
      ),
    );

    render(
      <MantineProvider>
        <PullRequestPanel card={linked} enabled onChange={vi.fn()} />
      </MantineProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Sync PR" }));

    expect(
      await screen.findByText("GitHub rate limit reached; retry in 60s"),
    ).toBeTruthy();
  });

  it("has no Sync PR without a linked PR", () => {
    render(
      <MantineProvider>
        <PullRequestPanel card={card} enabled onChange={vi.fn()} />
      </MantineProvider>,
    );
    expect(screen.queryByRole("button", { name: "Sync PR" })).toBeNull();
  });

  it("warns about a PR closed without merging", () => {
    render(
      <MantineProvider>
        <PullRequestPanel
          card={{ ...linked, githubPrState: "closed" }}
          enabled
          onChange={vi.fn()}
        />
      </MantineProvider>,
    );
    expect(screen.getByText("PR closed")).toBeTruthy();
  });

  it("shows the synced title, branches and description as Markdown", () => {
    render(
      <MantineProvider>
        <LinkedPullRequest
          card={{
            ...linked,
            githubPrTitle: "Sync linked PRs",
            githubPrBody: "## Why\n\nMerges **show up** by themselves.",
            githubPrHead: "issue-31",
            githubPrBase: "main",
            githubPrHeadSha: "abcdef0123456789",
            githubPrReviewDecision: "changes_requested",
          }}
        />
      </MantineProvider>,
    );
    expect(screen.getByText("Sync linked PRs")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Why" })).toBeTruthy();
    expect(screen.getByText("show up").tagName).toBe("STRONG");
    expect(screen.getByText("abcdef0")).toBeTruthy();
    expect(screen.getByText("Changes requested")).toBeTruthy();
  });

  it("syncs on open only a PR that may still change and was not just synced", () => {
    const now = new Date("2026-10-29T08:05:00.000Z").getTime();
    expect(shouldSyncOnOpen(linked, now)).toBe(true);
    expect(
      shouldSyncOnOpen(
        { ...linked, githubPrSyncedAt: "2026-10-29T08:04:30.000Z" },
        now,
      ),
    ).toBe(false);
    expect(shouldSyncOnOpen({ ...linked, githubPrState: "merged" }, now)).toBe(
      false,
    );
    expect(shouldSyncOnOpen(card, now)).toBe(false);
  });
});
