import {
  Alert,
  Anchor,
  Badge,
  Button,
  Code,
  Group,
  Modal,
  Paper,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { Bot, Pause, Play, Square, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { AutomationState, Worker, WorkerMode } from "../shared/types.js";
import {
  elapsedSince,
  formatElapsed,
  formatLastSeen,
  offlineWorkers,
  onlineWorkers,
  workerInstanceLabel,
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

const workerRoles: Record<WorkerMode, string> = {
  implement: "",
  review: "reviewer",
  rebase: "rebaser",
};
const workerModeColors: Record<WorkerMode, string> = {
  implement: "blue",
  review: "cyan",
  rebase: "indigo",
};
const workerActivityPrefixes: Record<WorkerMode, string> = {
  implement: "",
  review: "reviewing ",
  rebase: "rebasing ",
};

const workerStepLabel = (worker: Worker) => {
  if (worker.mode === "review") {
    if (worker.step === "agent_running") return "Reviewing";
    if (worker.step === "handing_over") return "Submitting verdict";
  }
  if (worker.mode === "rebase") {
    if (worker.step === "agent_running") return "Rebasing";
    if (worker.step === "handing_over") return "Reporting the rebase";
  }
  return worker.step ? workerStepLabels[worker.step] : "";
};

/** The live "being worked on" line on a board card. */
export const WorkerActivity = ({ worker }: { worker: Worker }) => {
  const now = useNow();
  return (
    <Group
      className="board-card-worker"
      gap={8}
      wrap="nowrap"
      align="flex-start"
      aria-label={`Being worked on by ${worker.name}`}
    >
      <span className="worker-live-dot" aria-hidden />
      <Stack gap={0} style={{ minWidth: 0 }}>
        <Text size="xs" fw={600} truncate>
          {worker.name}
        </Text>
        <Group gap={6} wrap="nowrap">
          {worker.step && (
            <Text size="xs" c="dimmed" className="board-card-worker-step">
              {workerStepLabel(worker)}
            </Text>
          )}
          {worker.taskStartedAt && (
            <Text size="xs" c="dimmed" className="board-card-worker-elapsed">
              {formatElapsed(elapsedSince(worker.taskStartedAt, now))}
            </Text>
          )}
        </Group>
      </Stack>
    </Group>
  );
};

type WorkersListProps = {
  workers: Worker[];
  onOpenTask: (taskId: string) => void;
  /** Asks a worker to stop; omitted where the list is read-only. */
  onStop?: (worker: Worker) => Promise<void>;
  /** Removes an offline worker; omitted where the list is read-only. */
  onRemove?: (worker: Worker) => Promise<void>;
};

export const WorkersList = ({
  workers,
  onOpenTask,
  onStop,
  onRemove,
}: WorkersListProps) => {
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
                  {[
                    worker.agent,
                    worker.machine,
                    workerRoles[worker.mode ?? "implement"],
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </Text>
                {/* Restarts under one name look alike otherwise. */}
                <Text size="xs" c="dimmed" className="worker-instance">
                  <span title={worker.instance ? "Instance" : "Worker id"}>
                    {workerInstanceLabel(worker)}
                  </span>
                  {" · "}
                  <span title={new Date(worker.startedAt).toLocaleString()}>
                    started {formatLastSeen(worker.startedAt, now)}
                  </span>
                </Text>
              </Stack>
              <Group gap={6} wrap="nowrap">
                <Badge
                  variant="outline"
                  color={workerModeColors[worker.mode ?? "implement"]}
                >
                  {worker.mode ?? "implement"}
                </Badge>
                {worker.stopRequestedAt && worker.status !== "offline" ? (
                  <Badge className="worker-status" variant="light" color="red">
                    Stopping
                  </Badge>
                ) : (
                  <Badge
                    className="worker-status"
                    variant="light"
                    color={workerStateColors[worker.status]}
                  >
                    {workerStateLabels[worker.status]}
                  </Badge>
                )}
                {onStop &&
                  worker.status !== "offline" &&
                  !worker.stopRequestedAt && (
                    <Button
                      type="button"
                      size="compact-xs"
                      variant="subtle"
                      color="red"
                      leftSection={<Square size={10} aria-hidden />}
                      aria-label={`Stop ${worker.name}`}
                      onClick={() => void onStop(worker)}
                    >
                      Stop
                    </Button>
                  )}
                {/* A silent worker that never signed off still holds its claims; removing
                    it gives them back, also after a stop request it went silent without
                    answering. */}
                {onRemove && worker.status === "offline" && (
                  <Button
                    type="button"
                    size="compact-xs"
                    variant="subtle"
                    color="red"
                    leftSection={<Trash2 size={10} aria-hidden />}
                    aria-label={`Remove ${worker.name} (${workerInstanceLabel(worker)})`}
                    onClick={() => void onRemove(worker)}
                  >
                    Remove
                  </Button>
                )}
              </Group>
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
                  {workerActivityPrefixes[worker.mode ?? "implement"]}
                  {[worker.taskKey, worker.taskTitle].filter(Boolean).join(" ")}
                </Anchor>
                {worker.step && (
                  <Badge size="sm" variant="outline" color="blue">
                    {workerStepLabel(worker)}
                  </Badge>
                )}
                {worker.taskStartedAt && (
                  <Text size="xs" c="dimmed">
                    {formatElapsed(elapsedSince(worker.taskStartedAt, now))}
                  </Text>
                )}
                {worker.runId && (
                  <Code
                    className="worker-run-id"
                    title={`vp task logs ${worker.runId}`}
                  >
                    {worker.runId}
                  </Code>
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

type AutomationControlProps = {
  automation: AutomationState | null;
  onPause: (reason: string) => Promise<void>;
  onResume: () => Promise<void>;
};

/** Pause or resume automation of the project; runs in progress finish. */
export const AutomationControl = ({
  automation,
  onPause,
  onResume,
}: AutomationControlProps) => {
  const [reason, setReason] = useState("");
  if (automation?.paused) {
    return (
      <Alert
        className="automation-paused"
        color="yellow"
        variant="light"
        title="Automation paused"
      >
        <Stack gap="xs" align="flex-start">
          <Text size="sm">
            {automation.reason ??
              "Workers take no new tasks until automation is resumed."}
          </Text>
          <Button
            type="button"
            size="xs"
            variant="light"
            leftSection={<Play size={14} aria-hidden />}
            onClick={() => void onResume()}
          >
            Resume automation
          </Button>
        </Stack>
      </Alert>
    );
  }
  return (
    <Group align="flex-end" gap="xs" className="automation-control">
      <TextInput
        style={{ flex: 1 }}
        size="xs"
        label="Pause reason"
        placeholder="Optional"
        value={reason}
        onChange={(event) => setReason(event.currentTarget.value)}
      />
      <Button
        type="button"
        size="xs"
        variant="light"
        color="yellow"
        leftSection={<Pause size={14} aria-hidden />}
        onClick={() => {
          void onPause(reason.trim());
          setReason("");
        }}
      >
        Pause automation
      </Button>
    </Group>
  );
};

type RemoveOfflineWorkersProps = {
  count: number;
  onRemoveOffline: () => Promise<void>;
};

/** Removes every offline worker of the list, once confirmed in place. */
export const RemoveOfflineWorkers = ({
  count,
  onRemoveOffline,
}: RemoveOfflineWorkersProps) => {
  const [confirming, setConfirming] = useState(false);
  if (count === 0) return null;
  if (!confirming) {
    return (
      <Group justify="flex-end">
        <Button
          type="button"
          size="xs"
          variant="subtle"
          color="red"
          leftSection={<Trash2 size={14} aria-hidden />}
          onClick={() => setConfirming(true)}
        >
          Remove offline workers
        </Button>
      </Group>
    );
  }
  return (
    <Alert
      className="remove-offline-workers"
      color="red"
      variant="light"
      title={`Remove ${count} offline ${count === 1 ? "worker" : "workers"}?`}
    >
      <Stack gap="xs" align="flex-start">
        <Text size="sm">
          Tasks they still hold go back to Planned. Their run reports and task
          history stay.
        </Text>
        <Group gap="xs">
          <Button
            type="button"
            size="xs"
            color="red"
            onClick={() => {
              setConfirming(false);
              void onRemoveOffline();
            }}
          >
            Remove
          </Button>
          <Button
            type="button"
            size="xs"
            variant="default"
            onClick={() => setConfirming(false)}
          >
            Keep them
          </Button>
        </Group>
      </Stack>
    </Alert>
  );
};

type WorkersIndicatorProps = WorkersListProps &
  AutomationControlProps &
  Pick<RemoveOfflineWorkersProps, "onRemoveOffline"> & {
    onStop: (worker: Worker) => Promise<void>;
    onRemove: (worker: Worker) => Promise<void>;
  };

/**
 * The project's workers in the header: how many are connected and working,
 * with the list and the automation controls on click. Shown before any worker
 * connects too, so automation can be paused ahead of the first one.
 */
export const WorkersIndicator = ({
  workers,
  automation,
  onOpenTask,
  onStop,
  onRemove,
  onRemoveOffline,
  onPause,
  onResume,
}: WorkersIndicatorProps) => {
  const [opened, setOpened] = useState(false);
  const paused = Boolean(automation?.paused);
  const online = onlineWorkers(workers);
  const working = online.filter((worker) => worker.status === "working");
  const counted =
    online.length > 0
      ? `${online.length} ${online.length === 1 ? "worker" : "workers"}${
          working.length > 0 ? ` · ${working.length} working` : ""
        }`
      : workers.length > 0
        ? "Workers offline"
        : "No workers";
  const label = paused ? `Automation paused · ${counted}` : counted;
  return (
    <>
      <Button
        className="workers-indicator"
        type="button"
        variant="light"
        color={paused ? "yellow" : online.length > 0 ? "teal" : "gray"}
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
        <Stack gap="md">
          <AutomationControl
            automation={automation}
            onPause={onPause}
            onResume={onResume}
          />
          <RemoveOfflineWorkers
            count={offlineWorkers(workers).length}
            onRemoveOffline={onRemoveOffline}
          />
          {workers.length === 0 ? (
            <Text c="dimmed" size="sm">
              No worker has connected in the last day.
            </Text>
          ) : (
            <WorkersList
              workers={workers}
              onStop={onStop}
              onRemove={onRemove}
              onOpenTask={(taskId) => {
                setOpened(false);
                onOpenTask(taskId);
              }}
            />
          )}
        </Stack>
      </Modal>
    </>
  );
};
