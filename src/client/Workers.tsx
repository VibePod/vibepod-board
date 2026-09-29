import {
  Anchor,
  Badge,
  Button,
  Group,
  Modal,
  Paper,
  Stack,
  Text,
} from "@mantine/core";
import { Bot } from "lucide-react";
import { useEffect, useState } from "react";
import type { Worker } from "../shared/types.js";
import {
  elapsedSince,
  formatElapsed,
  formatLastSeen,
  onlineWorkers,
  workerStateColors,
  workerStateLabels,
  workerStepLabels,
} from "./workerUtils.js";

/** The current time, ticking every `intervalMs`, for clocks that count up. */
export const useNow = (intervalMs = 1000): number => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
};

/** The live "being worked on" line on a board card. */
export const WorkerActivity = ({ worker }: { worker: Worker }) => {
  const now = useNow();
  return (
    <Group
      className="board-card-worker"
      gap={6}
      wrap="nowrap"
      aria-label={`Being worked on by ${worker.name}`}
    >
      <span className="worker-live-dot" aria-hidden />
      <Text size="xs" fw={600} truncate>
        {worker.name}
      </Text>
      {worker.step && (
        <Text size="xs" c="dimmed" className="board-card-worker-step">
          {workerStepLabels[worker.step]}
        </Text>
      )}
      {worker.taskStartedAt && (
        <Text size="xs" c="dimmed" className="board-card-worker-elapsed">
          {formatElapsed(elapsedSince(worker.taskStartedAt, now))}
        </Text>
      )}
    </Group>
  );
};

type WorkersListProps = {
  workers: Worker[];
  onOpenTask: (taskId: string) => void;
};

export const WorkersList = ({ workers, onOpenTask }: WorkersListProps) => {
  const now = useNow();
  return (
    <Stack gap="sm" className="workers-list">
      {workers.map((worker) => (
        <Paper
          key={worker.id}
          className="worker-row"
          withBorder
          radius="md"
          p="sm"
        >
          <Stack gap={6}>
            <Group justify="space-between" align="flex-start" wrap="nowrap">
              <Stack gap={0}>
                <Text fw={700}>{worker.name}</Text>
                <Text size="xs" c="dimmed">
                  {[worker.agent, worker.machine].filter(Boolean).join(" · ")}
                </Text>
              </Stack>
              <Badge
                className="worker-status"
                variant="light"
                color={workerStateColors[worker.status]}
              >
                {workerStateLabels[worker.status]}
              </Badge>
            </Group>
            {worker.status === "paused" && worker.statusReason && (
              <Text size="sm" className="worker-status-reason">
                {worker.statusReason}
              </Text>
            )}
            {worker.status === "working" && worker.taskId && (
              <Group gap="xs" wrap="wrap">
                <Anchor
                  component="button"
                  type="button"
                  size="sm"
                  onClick={() => onOpenTask(worker.taskId as string)}
                >
                  {[worker.taskKey, worker.taskTitle].filter(Boolean).join(" ")}
                </Anchor>
                {worker.step && (
                  <Badge size="sm" variant="outline" color="blue">
                    {workerStepLabels[worker.step]}
                  </Badge>
                )}
                {worker.taskStartedAt && (
                  <Text size="xs" c="dimmed">
                    {formatElapsed(elapsedSince(worker.taskStartedAt, now))}
                  </Text>
                )}
              </Group>
            )}
            <Text size="xs" c="dimmed">
              Last seen {formatLastSeen(worker.lastSeenAt, now)}
            </Text>
          </Stack>
        </Paper>
      ))}
    </Stack>
  );
};

type WorkersIndicatorProps = WorkersListProps;

/**
 * The project's workers in the header: how many are connected and working,
 * with the full list on click. Hidden while no worker has been seen.
 */
export const WorkersIndicator = ({
  workers,
  onOpenTask,
}: WorkersIndicatorProps) => {
  const [opened, setOpened] = useState(false);
  if (workers.length === 0) {
    return null;
  }
  const online = onlineWorkers(workers);
  const working = online.filter((worker) => worker.status === "working");
  const label =
    online.length > 0
      ? `${online.length} ${online.length === 1 ? "worker" : "workers"}${
          working.length > 0 ? ` · ${working.length} working` : ""
        }`
      : "Workers offline";
  return (
    <>
      <Button
        className="workers-indicator"
        type="button"
        variant="light"
        color={online.length > 0 ? "teal" : "gray"}
        leftSection={<Bot size={16} aria-hidden />}
        onClick={() => setOpened(true)}
      >
        {label}
      </Button>
      <Modal
        opened={opened}
        onClose={() => setOpened(false)}
        title="Workers"
        centered
        size="lg"
      >
        <WorkersList
          workers={workers}
          onOpenTask={(taskId) => {
            setOpened(false);
            onOpenTask(taskId);
          }}
        />
      </Modal>
    </>
  );
};
