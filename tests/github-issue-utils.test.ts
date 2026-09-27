import { describe, expect, it } from "vitest";

import {
  formatSyncedAgo,
  isStaleSyncError,
  issueLinkForIdea,
} from "../src/client/githubIssue.js";

describe("GitHub issue helpers", () => {
  it("builds a link only for linked tasks", () => {
    expect(issueLinkForIdea({})).toBeNull();
    expect(
      issueLinkForIdea({
        githubIssueUrl: "https://github.com/o/r/issues/4",
        githubIssueNumber: 4,
        githubRepository: "o/r",
        githubIssueState: "closed",
      }),
    ).toEqual({
      url: "https://github.com/o/r/issues/4",
      number: 4,
      repository: "o/r",
      state: "closed",
    });
  });

  it("formats the time since the last sync", () => {
    const now = Date.parse("2026-09-27T12:00:00.000Z");
    expect(formatSyncedAgo("2026-09-27T11:59:30.000Z", now)).toBe("just now");
    expect(formatSyncedAgo("2026-09-27T11:55:00.000Z", now)).toBe("5 min ago");
    expect(formatSyncedAgo("2026-09-27T09:00:00.000Z", now)).toBe("3 h ago");
    expect(formatSyncedAgo("2026-09-25T12:00:00.000Z", now)).toBe("2 d ago");
  });

  it("recognises the stale-push conflict", () => {
    expect(
      isStaleSyncError(
        "Issue changed on GitHub since the last sync — pull first",
      ),
    ).toBe(true);
    expect(isStaleSyncError("GitHub rejected the token")).toBe(false);
  });
});
