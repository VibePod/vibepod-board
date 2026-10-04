// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PullRequestPanel,
  SuggestedPullRequest,
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
  it("prefills the dialog from the draft and opens the PR", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === "/api/board/card-1/pull-request" && !init?.method) {
          // The draft comes back as is, not wrapped in `item`.
          return jsonResponse({
            repository: "vibepod/vibepod-board",
            title: "Open PRs from the board",
            base: "main",
            bases: ["main"],
            body: "Closes #33",
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
      ).toBe("Open PRs from the board"),
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
      title: "Open PRs from the board",
      base: "main",
      body: "Closes #33",
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
    await userEvent.click(screen.getByRole("radio", { name: "Preview" }));
    expect(screen.getByText("Open PR").tagName).toBe("STRONG");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const patch = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "PATCH",
    );
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
      prTitle: "Open pull requests",
      prBody: "Adds the **Open PR** dialog.",
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
