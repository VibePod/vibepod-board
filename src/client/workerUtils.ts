import type { Worker, WorkerState, WorkerStep } from "../shared/types.js";

export const workerStepLabels: Record<WorkerStep, string> = {
  preparing_workspace: "Preparing workspace",
  agent_running: "Agent running",
  verifying: "Verifying",
  handing_over: "Handing over",
};

export const workerStateColors: Record<WorkerState, string> = {
  idle: "teal",
  working: "blue",
  paused: "yellow",
  offline: "gray",
};

export const workerStateLabels: Record<WorkerState, string> = {
  idle: "Idle",
  working: "Working",
  paused: "Paused",
  offline: "Offline",
};

/** Elapsed time as a clock: "4:05", or "1:02:03" past the hour. */
export const formatElapsed = (seconds: number): string => {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, "0");
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}`
    : `${minutes}:${rest}`;
};

export const elapsedSince = (since: string, nowMs: number): number =>
  (nowMs - new Date(since).getTime()) / 1000;

/** "just now", "42 s ago", "5 min ago", "3 h ago", "2 d ago". */
export const formatLastSeen = (lastSeenAt: string, nowMs: number): string => {
  const seconds = Math.max(0, Math.floor(elapsedSince(lastSeenAt, nowMs)));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
};

/** Workers working on a task, by task id: they get the live indicator on the card. */
export const workingWorkersByTask = (workers: Worker[]): Map<string, Worker> =>
  new Map(
    workers
      .filter(
        (worker): worker is Worker & { taskId: string } =>
          worker.status === "working" && Boolean(worker.taskId),
      )
      .map((worker) => [worker.taskId, worker]),
  );

export const onlineWorkers = (workers: Worker[]): Worker[] =>
  workers.filter((worker) => worker.status !== "offline");

/**
 * What the workers are doing, as far as the board is concerned. When it
 * changes between two polls, automation moved cards and the board reloads.
 */
export const workerActivitySignature = (workers: Worker[]): string =>
  workers
    .map((worker) => `${worker.id}:${worker.status}:${worker.taskId ?? ""}`)
    .sort()
    .join("|");
