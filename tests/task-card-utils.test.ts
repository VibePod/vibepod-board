import { describe, expect, it } from "vitest";

import { isCardReadinessStale, isReadinessStale, readinessColor, taskListCardView } from "../src/client/taskCardUtils.js";
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

  it("maps readiness scores to badge colors", () => {
    expect(readinessColor(1)).toBe("red");
    expect(readinessColor(3)).toBe("red");
    expect(readinessColor(4)).toBe("yellow");
    expect(readinessColor(6)).toBe("yellow");
    expect(readinessColor(7)).toBe("green");
    expect(readinessColor(10)).toBe("green");
  });

  it("computes readiness staleness from timestamps", () => {
    const base = {
      updatedAt: "2026-07-14T10:00:00.000Z"
    };
    expect(isReadinessStale({ ...base, readinessEvaluatedAt: undefined })).toBe(false);
    expect(isReadinessStale({ ...base, readinessEvaluatedAt: "2026-07-14T10:00:00.000Z" })).toBe(false);
    expect(isReadinessStale({ ...base, readinessEvaluatedAt: "2026-07-14T11:00:00.000Z" })).toBe(false);
    expect(isReadinessStale({ ...base, readinessEvaluatedAt: "2026-07-14T09:00:00.000Z" })).toBe(true);
  });

  it("computes card staleness from the linked idea when present", () => {
    const ideaById = new Map([["idea-1", { updatedAt: "2026-07-14T12:00:00.000Z" }]]);

    // Card edited earlier than the idea; idea edited AFTER the score -> stale from the idea.
    const card = {
      updatedAt: "2026-07-14T09:00:00.000Z",
      readinessEvaluatedAt: "2026-07-14T10:00:00.000Z",
      ideaId: "idea-1"
    };
    expect(isCardReadinessStale(card, ideaById)).toBe(true);

    // No linked idea -> falls back to the card's own updatedAt (fresh here).
    const orphan = {
      updatedAt: "2026-07-14T09:00:00.000Z",
      readinessEvaluatedAt: "2026-07-14T10:00:00.000Z"
    };
    expect(isCardReadinessStale(orphan, ideaById)).toBe(false);

    // No score -> never stale.
    expect(isCardReadinessStale({ updatedAt: "x", ideaId: "idea-1" }, ideaById)).toBe(false);
  });
});
