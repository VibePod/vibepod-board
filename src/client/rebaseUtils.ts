import type { BoardCard, BoardColumn, RebaseTarget } from "../shared/types.js";

/** Columns whose cards carry a branch worth rebasing. */
export const rebaseColumns: readonly BoardColumn[] = [
  "planned",
  "review",
  "pr_ready",
];

export const rebaseTargetLabels: Record<RebaseTarget, string> = {
  both: "Origin's task branch, then the default branch",
  origin: "Origin's task branch",
  default_branch: "The default branch",
};

/** Short form for the card: "onto origin and the default branch". */
export const rebaseTargetShortLabels: Record<RebaseTarget, string> = {
  both: "origin and the default branch",
  origin: "origin's task branch",
  default_branch: "the default branch",
};

/**
 * Whether the board can ask for a rebase of the card's branch: it has one, sits
 * in Planned, Review or PR ready, no run holds it, it waits for no answer, and
 * no worker is rebasing it already. A request nobody took yet can be asked for
 * again, to change its target.
 */
export const canRequestRebase = (
  card: BoardCard,
  now: number = Date.now(),
): boolean =>
  Boolean(card.ideaId) &&
  Boolean(card.branchName) &&
  !card.archivedAt &&
  rebaseColumns.includes(card.column) &&
  !card.claimedAt &&
  !card.question &&
  !isRebaseHeld(card, now);

/** A worker holds the request until its lease runs out. */
export const isRebaseHeld = (
  card: BoardCard,
  now: number = Date.now(),
): boolean =>
  Boolean(card.rebaseTarget) &&
  Boolean(card.rebaseHolder) &&
  card.rebaseExpiresAt !== undefined &&
  Date.parse(card.rebaseExpiresAt) > now;

/**
 * A block a rebase is the likely fix for, such as a branch that diverged from
 * origin's or fell behind the default branch.
 */
export const isBlockedForRebase = (card: BoardCard): boolean =>
  Boolean(card.blockedAt) &&
  /diverged|behind|rebase|conflict/i.test(card.blockedReason ?? "");

/** The line a card with a pending rebase shows. */
export const rebaseStatusText = (
  card: BoardCard,
  now: number = Date.now(),
): string => {
  if (!card.rebaseTarget) return "";
  const onto = `onto ${rebaseTargetShortLabels[card.rebaseTarget]}`;
  if (isRebaseHeld(card, now)) {
    return `${card.rebaseHolder} is rebasing ${onto}`;
  }
  const by = card.rebaseRequestedBy ? ` by ${card.rebaseRequestedBy}` : "";
  return `Rebase ${onto} requested${by}; waiting for a rebase worker`;
};
