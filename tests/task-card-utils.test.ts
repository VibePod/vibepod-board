import { describe, expect, it } from "vitest";

import { taskListCardView } from "../src/client/taskCardUtils.js";
import type { BoardColumns, Idea } from "../src/shared/types.js";

const task = (patch: Partial<Idea> = {}): Idea => ({
  id: "idea-1",
  projectId: "project-1",
  title: "Compact task card",
  summary: "This should not be shown in the list card",
  details: "Long description stays available in the edit modal",
  status: "ready",
  taskNumber: 7,
  labels: ["ui", "board"],
  acceptanceCriteria: [],
  createdAt: "2026-06-04T00:00:00.000Z",
  updatedAt: "2026-06-04T00:00:00.000Z",
  ...patch
});

const columns: BoardColumns = {
  ready: [
    {
      id: "card-1",
      projectId: "project-1",
      title: "Compact task card",
      details: "",
      column: "ready",
      ideaId: "idea-1",
      labels: [],
      createdAt: "2026-06-04T00:00:00.000Z",
      updatedAt: "2026-06-04T00:00:00.000Z"
    }
  ],
  planned: [],
  in_progress: [],
  review: [],
  done: []
};

describe("task card utilities", () => {
  it("builds compact task list card data without summary or details", () => {
    const view = taskListCardView(task(), columns, "APP");

    expect(view).toEqual({
      id: "idea-1",
      taskId: "APP-7",
      title: "Compact task card",
      status: "ready",
      labels: ["ui", "board"],
      isReady: true
    });
    expect(Object.keys(view)).not.toContain("summary");
    expect(Object.keys(view)).not.toContain("details");
  });
});
