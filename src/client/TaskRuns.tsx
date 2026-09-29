import { Badge, Code, Group, Paper, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";
import type { TaskRun } from "../shared/types.js";
import { api } from "./api.js";
import {
  formatDuration,
  runOutcomeColors,
  runOutcomeLabels,
} from "./automationUtils.js";
import { MarkdownText } from "./Markdown.js";

type TaskRunsProps = {
  ideaId: string;
  /** Reloads the reports when it changes, such as the card's `updatedAt`. */
  reloadKey?: string;
};

const formatRunTime = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));

const RunReport = ({ run }: { run: TaskRun }) => (
  <Paper className="task-run" withBorder radius="md" p="sm">
    <Stack gap="xs">
      <Group gap="xs" justify="space-between" align="flex-start">
        <Group gap="xs">
          <Badge
            className="task-run-outcome"
            variant="light"
            color={runOutcomeColors[run.outcome] ?? "gray"}
          >
            {runOutcomeLabels[run.outcome] ?? run.outcome}
          </Badge>
          <Text size="sm" fw={600}>
            {[run.workerName, run.agent].filter(Boolean).join(" · ") ||
              "Runner"}
          </Text>
        </Group>
        <Text size="xs" c="dimmed">
          {[
            formatRunTime(run.startedAt ?? run.createdAt),
            run.durationSeconds !== undefined
              ? formatDuration(run.durationSeconds)
              : "",
          ]
            .filter(Boolean)
            .join(" · ")}
        </Text>
      </Group>
      {run.failureReason && (
        <Text size="sm" c="red" className="task-run-failure">
          {run.failureReason}
        </Text>
      )}
      {run.summary && (
        <div className="overview-markdown task-run-summary">
          <MarkdownText>{run.summary}</MarkdownText>
        </div>
      )}
      {run.commits.length > 0 && (
        <Stack gap={2} className="task-run-commits">
          <Text size="xs" fw={700} c="dimmed">
            {run.commits.length === 1
              ? "1 commit"
              : `${run.commits.length} commits`}
            {run.branchName ? ` on ${run.branchName}` : ""}
          </Text>
          {run.commits.map((commit) => (
            <Group key={commit.sha} gap={6} wrap="nowrap">
              <Code>{commit.sha.slice(0, 8)}</Code>
              <Text size="sm" truncate>
                {commit.subject}
              </Text>
            </Group>
          ))}
        </Stack>
      )}
      {run.verifyCommand && (
        <details className="task-run-verify">
          <summary>
            <Text component="span" size="sm">
              Verify <Code>{run.verifyCommand}</Code>{" "}
              {run.verifyExitCode !== undefined && (
                <Badge
                  size="xs"
                  variant="light"
                  color={run.verifyExitCode === 0 ? "teal" : "red"}
                >
                  exit {run.verifyExitCode}
                </Badge>
              )}
              {run.verifyOutputTruncated ? " (output shortened)" : ""}
            </Text>
          </summary>
          {run.verifyOutput && (
            <pre className="task-run-verify-output">{run.verifyOutput}</pre>
          )}
        </details>
      )}
    </Stack>
  </Paper>
);

/**
 * Every automated run of a task, newest first: the history of its attempts.
 * Hidden while the task has none.
 */
export const TaskRuns = ({ ideaId, reloadKey }: TaskRunsProps) => {
  // Kept with the task they belong to, so switching tasks never shows the last
  // task's runs while the new ones load.
  const [loaded, setLoaded] = useState<{
    ideaId: string;
    runs: TaskRun[];
  } | null>(null);
  const [error, setError] = useState("");
  const runs = loaded?.ideaId === ideaId ? loaded.runs : null;

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadKey only triggers a refetch
  useEffect(() => {
    let cancelled = false;
    setError("");
    api<{ items: TaskRun[] }>(`/api/ideas/${ideaId}/runs`)
      .then((response) => {
        if (!cancelled) setLoaded({ ideaId, runs: response.items });
      })
      .catch((requestError: Error) => {
        if (!cancelled) setError(requestError.message);
      });
    return () => {
      cancelled = true;
    };
  }, [ideaId, reloadKey]);

  if (error) {
    return (
      <Text size="sm" c="dimmed">
        Run reports unavailable: {error}
      </Text>
    );
  }
  if (!runs || runs.length === 0) {
    return null;
  }
  return (
    <Paper className="overview-section task-runs" withBorder radius="md" p="md">
      <Group justify="space-between" mb="xs">
        <Text size="xs" fw={700} tt="uppercase" c="dimmed">
          Automated Runs
        </Text>
        <Badge variant="light" color="gray">
          {runs.length}
        </Badge>
      </Group>
      <Stack gap="sm">
        {runs.map((run) => (
          <RunReport key={run.id} run={run} />
        ))}
      </Stack>
    </Paper>
  );
};
