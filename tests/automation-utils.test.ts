import { describe, expect, it } from "vitest";

import {
  failedAttemptsLabel,
  formatClaimedSince,
  formatDuration,
  isCardBlocked,
  pluralize,
} from "../src/client/automationUtils.js";

describe("automation labels", () => {
  it("counts failed attempts", () => {
    expect(failedAttemptsLabel(1)).toBe("1 failed attempt");
    expect(failedAttemptsLabel(2)).toBe("2 failed attempts");
    expect(pluralize(3, "dependency", "dependencies")).toBe("3 dependencies");
  });

  it("tells a blocked card from one that is not", () => {
    expect(isCardBlocked({ blockedAt: "2026-09-29T10:00:00.000Z" })).toBe(true);
    expect(isCardBlocked({})).toBe(false);
  });

  it("names the time of day for a claim taken today", () => {
    const now = new Date(2026, 8, 29, 16, 0);
    const claimedAt = new Date(2026, 8, 29, 14, 5).toISOString();
    expect(formatClaimedSince(claimedAt, now, "en-GB")).toBe("since 14:05");
  });

  it("adds the date for an older claim", () => {
    const now = new Date(2026, 8, 29, 16, 0);
    const claimedAt = new Date(2026, 8, 28, 9, 30).toISOString();
    expect(formatClaimedSince(claimedAt, now, "en-GB")).toMatch(
      /^since 28 Sept?, 09:30$/,
    );
  });
});

describe("run durations", () => {
  it("reads like a sentence", () => {
    expect(formatDuration(45)).toBe("45 s");
    expect(formatDuration(725)).toBe("12 min 5 s");
    expect(formatDuration(600)).toBe("10 min");
    expect(formatDuration(7380)).toBe("2 h 3 min");
    expect(formatDuration(7200)).toBe("2 h");
  });
});
