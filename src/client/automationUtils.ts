import type { BoardCard, RunOutcome, TaskEventKind } from "../shared/types.js";

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
  cancelled: "Cancelled",
  question: "Question",
  answer: "Answer",
  feedback: "Review feedback",
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
  cancelled: "gray",
  question: "violet",
  answer: "blue",
  feedback: "orange",
};

export const runOutcomeLabels: Record<RunOutcome, string> = {
  done: "Done",
  failed: "Failed",
  timed_out: "Timed out",
  cancelled: "Cancelled",
  usage_limit: "Usage limit",
  needs_input: "Needs input",
};

export const runOutcomeColors: Record<RunOutcome, string> = {
  done: "teal",
  failed: "red",
  timed_out: "orange",
  cancelled: "gray",
  usage_limit: "yellow",
  needs_input: "violet",
};

/** "45 s", "12 min 5 s", "2 h 3 min". */
export const formatDuration = (seconds: number): string => {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  if (hours > 0)
    return minutes > 0 ? `${hours} h ${minutes} min` : `${hours} h`;
  if (minutes > 0)
    return rest > 0 ? `${minutes} min ${rest} s` : `${minutes} min`;
  return `${rest} s`;
};
