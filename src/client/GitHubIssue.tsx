import { Alert, Badge, Button, Group, Text, Tooltip } from "@mantine/core";
import {
  CircleCheck,
  CircleDot,
  CloudDownload,
  CloudUpload,
} from "lucide-react";
import { useState } from "react";
import type { Idea } from "../shared/types.js";
import {
  formatSyncedAgo,
  type GitHubIssueLink,
  type GitHubStatus,
  isStaleSyncError,
  issueLinkForIdea,
} from "./githubIssue.js";

/** Opens the linked issue in a new tab; green while open, violet once closed. */
export const GitHubIssueBadge = ({
  link,
  size = "sm",
}: {
  link: GitHubIssueLink;
  size?: "xs" | "sm";
}) => {
  const closed = link.state === "closed";
  const Icon = closed ? CircleCheck : CircleDot;
  const label = link.repository
    ? `${link.repository}#${link.number}`
    : `#${link.number}`;
  return (
    <Badge
      component="a"
      href={link.url}
      target="_blank"
      rel="noopener noreferrer"
      variant="light"
      color={closed ? "violet" : link.state === "open" ? "green" : "gray"}
      size={size}
      leftSection={<Icon size={12} aria-hidden />}
      title={`Open ${label} on GitHub${link.state ? ` (${link.state})` : ""}`}
      aria-label={`Open GitHub issue ${label} in a new tab`}
      style={{ cursor: "pointer", textTransform: "none" }}
      // Cards open the task on click; following the link must not do that too.
      onClick={(event) => event.stopPropagation()}
    >
      #{link.number}
    </Badge>
  );
};

type SyncAction = "push" | "pull";

/** Push the task to its GitHub issue or pull the issue back, with inline feedback. */
export const GitHubSyncPanel = ({
  idea,
  status,
  onSync,
}: {
  idea: Idea;
  status: GitHubStatus | null;
  onSync: (action: SyncAction) => Promise<void>;
}) => {
  const [pending, setPending] = useState<SyncAction | null>(null);
  const [error, setError] = useState("");
  const link = issueLinkForIdea(idea);
  const enabled = status?.enabled ?? false;

  const run = async (action: SyncAction) => {
    setPending(action);
    setError("");
    try {
      await onSync(action);
    } catch (err) {
      setError(err instanceof Error ? err.message : "GitHub sync failed");
    } finally {
      setPending(null);
    }
  };

  const disabledReason = !enabled
    ? "Set GITHUB_TOKEN to enable GitHub sync"
    : undefined;

  return (
    <Group gap="sm" align="center" wrap="wrap">
      {link && <GitHubIssueBadge link={link} />}
      <Tooltip label={disabledReason} disabled={!disabledReason}>
        <Button
          type="button"
          size="xs"
          variant="light"
          leftSection={<CloudUpload size={14} />}
          loading={pending === "push"}
          disabled={!enabled || pending !== null}
          onClick={() => void run("push")}
        >
          {link ? "Push to GitHub" : "Create GitHub issue"}
        </Button>
      </Tooltip>
      <Tooltip
        label={disabledReason ?? "Link a GitHub issue first"}
        disabled={enabled && !!link}
      >
        <Button
          type="button"
          size="xs"
          variant="default"
          leftSection={<CloudDownload size={14} />}
          loading={pending === "pull"}
          disabled={!enabled || !link || pending !== null}
          onClick={() => void run("pull")}
        >
          Pull from GitHub
        </Button>
      </Tooltip>
      {idea.githubSyncedAt && (
        <Text size="xs" c="dimmed">
          Synced {formatSyncedAgo(idea.githubSyncedAt)}
        </Text>
      )}
      {error && (
        <Alert color="red" variant="light" w="100%" p="xs">
          <Group gap="xs" justify="space-between">
            <Text size="sm">{error}</Text>
            {isStaleSyncError(error) && (
              <Button
                type="button"
                size="xs"
                variant="subtle"
                onClick={() => void run("pull")}
              >
                Pull now
              </Button>
            )}
          </Group>
        </Alert>
      )}
    </Group>
  );
};
