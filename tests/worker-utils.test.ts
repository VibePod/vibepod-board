import { describe, expect, it } from "vitest";
import {
  formatElapsed,
  formatLastSeen,
  onlineWorkers,
  workerActivitySignature,
  workingWorkersByTask,
} from "../src/client/workerUtils.js";
import type { Worker } from "../src/shared/types.js";

const worker = (patch: Partial<Worker>): Worker => ({
  id: "worker-1",
  projectId: "project-1",
  name: "claude@laptop",
  agent: "claude",
  machine: "laptop",
  status: "idle",
  startedAt: "2026-09-29T08:00:00.000Z",
  lastSeenAt: "2026-09-29T08:00:00.000Z",
  ...patch,
});

describe("worker helpers", () => {
  it("formats elapsed time as a clock", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(65)).toBe("1:05");
    expect(formatElapsed(3723)).toBe("1:02:03");
    expect(formatElapsed(-5)).toBe("0:00");
  });

  it("says when a worker was last seen", () => {
    const now = Date.parse("2026-09-29T08:00:00.000Z");
    expect(formatLastSeen("2026-09-29T08:00:00.000Z", now)).toBe("just now");
    expect(formatLastSeen("2026-09-29T07:59:18.000Z", now)).toBe("42 s ago");
    expect(formatLastSeen("2026-09-29T07:55:00.000Z", now)).toBe("5 min ago");
    expect(formatLastSeen("2026-09-29T05:00:00.000Z", now)).toBe("3 h ago");
  });

  it("maps working workers to their tasks", () => {
    const workers = [
      worker({ id: "a", status: "working", taskId: "idea-1" }),
      worker({ id: "b", status: "idle" }),
      worker({ id: "c", status: "offline", taskId: "idea-2" }),
    ];
    const byTask = workingWorkersByTask(workers);
    expect([...byTask.keys()]).toEqual(["idea-1"]);
    expect(onlineWorkers(workers).map((item) => item.id)).toEqual(["a", "b"]);
  });

  it("changes the activity signature only when status or task change", () => {
    const before = [worker({ status: "working", taskId: "idea-1" })];
    const stepped = [
      worker({ status: "working", taskId: "idea-1", step: "verifying" }),
    ];
    const done = [worker({ status: "idle" })];
    expect(workerActivitySignature(stepped)).toBe(
      workerActivitySignature(before),
    );
    expect(workerActivitySignature(done)).not.toBe(
      workerActivitySignature(before),
    );
  });
});
