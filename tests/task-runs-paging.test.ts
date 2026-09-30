import { describe, expect, it } from "vitest";
import { mergeNewestPage } from "../src/client/TaskRuns.js";
import type { TaskRun } from "../src/shared/types.js";

const run = (id: string, createdAt: string): TaskRun => ({
  id,
  ideaId: "idea-1",
  outcome: "failed",
  summary: "",
  commits: [],
  verifyOutputTruncated: false,
  createdAt,
});

const t = (minute: number) =>
  `2026-09-30T10:${String(minute).padStart(2, "0")}:00.000Z`;

describe("merging the newest page of run reports", () => {
  it("takes the page as it is on the first load", () => {
    const page = { items: [run("b", t(2)), run("a", t(1))], nextCursor: "a" };

    expect(mergeNewestPage(null, page)).toEqual({
      runs: page.items,
      nextCursor: "a",
    });
  });

  it("keeps older runs loaded beyond the newest page and their cursor", () => {
    const loaded = {
      runs: [run("c", t(3)), run("b", t(2)), run("a", t(1))],
      nextCursor: "a",
    };
    const page = { items: [run("d", t(4)), run("c", t(3))], nextCursor: "c" };

    expect(mergeNewestPage(loaded, page)).toEqual({
      runs: [run("d", t(4)), run("c", t(3)), run("b", t(2)), run("a", t(1))],
      nextCursor: "a",
    });
  });

  it("drops nothing when the newest page is empty", () => {
    expect(mergeNewestPage(null, { items: [] })).toEqual({
      runs: [],
      nextCursor: null,
    });
  });

  it("treats a page without items as empty", () => {
    expect(mergeNewestPage(null, {})).toEqual({ runs: [], nextCursor: null });
  });
});
