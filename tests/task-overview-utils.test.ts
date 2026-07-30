import { describe, expect, it } from "vitest";

import { taskOverviewForIdea } from "../src/client/taskOverviewUtils.js";
import type { Idea } from "../src/shared/types.js";

const task = (patch: Partial<Idea> = {}): Idea => ({
  id: "idea-1",
  projectId: "project-1",
  title: "Ship board overview",
  summary: "Fallback summary",
  details: "Read-only overview for board cards",
  status: "ready",
  taskNumber: 12,
  labels: ["board", "ui"],
  acceptanceCriteria: ["Board cards open read-only", "Edit is explicit"],
  createdAt: "2026-06-04T00:00:00.000Z",
  updatedAt: "2026-06-04T00:00:00.000Z",
  ...patch,
});

describe("task overview utilities", () => {
  it("builds read-only task overview data for the board modal", () => {
    expect(taskOverviewForIdea(task(), "APP")).toEqual({
      taskId: "APP-12",
      title: "Ship board overview",
      description: "Read-only overview for board cards",
      status: "ready",
      labels: ["board", "ui"],
      acceptanceCriteria: ["Board cards open read-only", "Edit is explicit"],
    });
  });

  it("falls back to summary when task details are empty", () => {
    expect(taskOverviewForIdea(task({ details: "" }), "APP").description).toBe(
      "Fallback summary",
    );
  });
});
