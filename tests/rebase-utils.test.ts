import { describe, expect, it } from "vitest";

import {
  canRequestRebase,
  isBlockedForRebase,
  isRebaseHeld,
  rebaseStatusText,
} from "../src/client/rebaseUtils.js";
import type { BoardCard } from "../src/shared/types.js";

const timestamp = "2026-10-29T08:00:00.000Z";
const now = Date.parse("2026-10-29T09:00:00.000Z");

const card = (patch: Partial<BoardCard>): BoardCard => ({
  id: "card-1",
  projectId: "project-1",
  ideaId: "idea-1",
  title: "Task",
  details: "",
  column: "planned",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  branchName: "vp-1",
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

describe("canRequestRebase", () => {
  it("allows cards with a branch in planned, review and PR ready", () => {
    expect(canRequestRebase(card({ column: "planned" }), now)).toBe(true);
    expect(canRequestRebase(card({ column: "review" }), now)).toBe(true);
    expect(canRequestRebase(card({ column: "pr_ready" }), now)).toBe(true);
  });

  it("refuses other columns, missing branches, runs and questions", () => {
    expect(canRequestRebase(card({ column: "ready" }), now)).toBe(false);
    expect(canRequestRebase(card({ column: "in_progress" }), now)).toBe(false);
    expect(canRequestRebase(card({ column: "done" }), now)).toBe(false);
    expect(canRequestRebase(card({ branchName: undefined }), now)).toBe(false);
    expect(canRequestRebase(card({ ideaId: undefined }), now)).toBe(false);
    expect(canRequestRebase(card({ claimedAt: timestamp }), now)).toBe(false);
    expect(canRequestRebase(card({ question: "Which?" }), now)).toBe(false);
    expect(canRequestRebase(card({ archivedAt: timestamp }), now)).toBe(false);
  });

  it("allows asking again until a worker holds the request", () => {
    const pending = card({
      rebaseTarget: "both",
      rebaseRequestedAt: timestamp,
    });
    expect(canRequestRebase(pending, now)).toBe(true);
    const held = {
      ...pending,
      rebaseHolder: "Claude::Rebaser",
      rebaseExpiresAt: "2026-10-29T10:00:00.000Z",
    };
    expect(isRebaseHeld(held, now)).toBe(true);
    expect(canRequestRebase(held, now)).toBe(false);
    // A lapsed hold leaves the request pending for the next worker.
    const lapsed = { ...held, rebaseExpiresAt: "2026-10-29T08:30:00.000Z" };
    expect(isRebaseHeld(lapsed, now)).toBe(false);
    expect(canRequestRebase(lapsed, now)).toBe(true);
  });
});

describe("isBlockedForRebase", () => {
  it("spots diverged and stale branches", () => {
    expect(
      isBlockedForRebase(
        card({
          blockedAt: timestamp,
          blockedReason: "The branch vp-1 on origin and here have diverged",
        }),
      ),
    ).toBe(true);
    expect(
      isBlockedForRebase(
        card({ blockedAt: timestamp, blockedReason: "Tests failed" }),
      ),
    ).toBe(false);
    expect(isBlockedForRebase(card({}))).toBe(false);
  });
});

describe("rebaseStatusText", () => {
  it("names the requester while pending and the holder while held", () => {
    const pending = card({
      rebaseTarget: "default_branch",
      rebaseRequestedBy: "admin",
      rebaseRequestedAt: timestamp,
    });
    expect(rebaseStatusText(pending, now)).toBe(
      "Rebase onto the default branch requested by admin; waiting for a rebase worker",
    );
    expect(
      rebaseStatusText(
        {
          ...pending,
          rebaseHolder: "Claude::Rebaser",
          rebaseExpiresAt: "2026-10-29T10:00:00.000Z",
        },
        now,
      ),
    ).toBe("Claude::Rebaser is rebasing onto the default branch");
    expect(rebaseStatusText(card({}), now)).toBe("");
  });
});
