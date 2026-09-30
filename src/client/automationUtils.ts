import type { BoardCard, TaskEventKind } from "../shared/types.js";

export const pluralize = (
  count: number,
  singular: string,
  plural = `${singular}s`,
): string => `${count} ${count === 1 ? singular : plural}`;

export const failedAttemptsLabel = (attempts: number): string =>
  pluralize(attempts, "failed attempt");

export const isCardBlocked = (card: Pick<BoardCard, "blockedAt">): boolean =>
  Boolean(card.blockedAt);

/**
 * When a claim was taken: the time of day for today, with the date otherwise,
 * such as "since 14:05" or "since 28 Sept, 14:05".
 */
export const formatClaimedSince = (
  claimedAt: string,
  now: Date = new Date(),
  locale?: string,
): string => {
  const date = new Date(claimedAt);
  const time = new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
  if (date.toDateString() === now.toDateString()) {
    return `since ${time}`;
  }
  const day = new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
  }).format(date);
  return `since ${day}, ${time}`;
};

export const taskEventLabels: Record<TaskEventKind, string> = {
  claimed: "Claimed",
  handed_over: "Handed over",
  failed: "Failed",
  blocked: "Blocked",
  released: "Released",
  expired: "Expired",
  claim_ended: "Claim ended",
  unblocked: "Unblocked",
};

export const taskEventColors: Record<TaskEventKind, string> = {
  claimed: "grape",
  handed_over: "teal",
  failed: "orange",
  blocked: "red",
  released: "gray",
  expired: "orange",
  claim_ended: "gray",
  unblocked: "blue",
};
