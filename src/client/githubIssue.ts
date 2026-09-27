import type { Idea } from "../shared/types.js";

export type GitHubStatus = {
  enabled: boolean;
  defaultRepository?: string;
};

export type GitHubIssueLink = {
  url: string;
  number: number;
  repository?: string;
  state?: "open" | "closed";
};

/** The issue a task links to, or null when it has none. */
export const issueLinkForIdea = (
  idea: Pick<
    Idea,
    | "githubIssueUrl"
    | "githubIssueNumber"
    | "githubRepository"
    | "githubIssueState"
  >,
): GitHubIssueLink | null =>
  idea.githubIssueUrl && idea.githubIssueNumber
    ? {
        url: idea.githubIssueUrl,
        number: idea.githubIssueNumber,
        repository: idea.githubRepository,
        state: idea.githubIssueState,
      }
    : null;

/** A pushed task that changed on GitHub since must be pulled before the next push. */
export const isStaleSyncError = (message: string) =>
  message.includes("pull first");

export const formatSyncedAgo = (syncedAt: string, now = Date.now()): string => {
  const seconds = Math.max(0, Math.round((now - Date.parse(syncedAt)) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} d ago`;
};
