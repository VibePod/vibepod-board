import { Badge, Group, Paper, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";
import type { TaskEvent } from "../shared/types.js";
import { api } from "./api.js";
import { taskEventColors, taskEventLabels } from "./automationUtils.js";

type TaskHistoryProps = {
  ideaId: string;
  /** Reloads the history when it changes, such as the card's `updatedAt`. */
  reloadKey?: string;
};

const formatEventTime = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));

/**
 * What happened to a task under automation, newest first. Tasks no runner has
 * touched have no history, and the section stays hidden.
 */
export const TaskHistory = ({ ideaId, reloadKey }: TaskHistoryProps) => {
  const [events, setEvents] = useState<TaskEvent[] | null>(null);
  const [error, setError] = useState("");

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadKey only triggers a refetch
  useEffect(() => {
    let cancelled = false;
    setError("");
    api<{ items: TaskEvent[] }>(`/api/ideas/${ideaId}/history`)
      .then((response) => {
        if (!cancelled) setEvents(response.items);
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
        History unavailable: {error}
      </Text>
    );
  }
  if (!events || events.length === 0) {
    return null;
  }
  return (
    <Paper
      className="overview-section task-history"
      withBorder
      radius="md"
      p="md"
    >
      <Text size="xs" fw={700} tt="uppercase" c="dimmed" mb="xs">
        History
      </Text>
      <Stack gap="xs">
        {events.map((event) => (
          <Group
            key={event.id}
            className="task-history-event"
            gap="xs"
            wrap="nowrap"
            align="flex-start"
          >
            <Badge
              variant="light"
              size="sm"
              color={taskEventColors[event.kind] ?? "gray"}
              style={{ flexShrink: 0 }}
            >
              {taskEventLabels[event.kind] ?? event.kind}
            </Badge>
            <Stack gap={0}>
              <Text size="sm" className="task-history-message">
                {event.message}
              </Text>
              <Text size="xs" c="dimmed">
                {[event.actor, formatEventTime(event.createdAt)]
                  .filter(Boolean)
                  .join(" · ")}
              </Text>
            </Stack>
          </Group>
        ))}
      </Stack>
    </Paper>
  );
};
