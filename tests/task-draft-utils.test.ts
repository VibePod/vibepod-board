import { describe, expect, it } from "vitest";

import {
  emptyTaskDraft,
  taskDraftToIdeaPayload,
  taskToDraft,
} from "../src/client/taskDraftUtils.js";
import type { Idea } from "../src/shared/types.js";

const task = (patch: Partial<Idea> = {}): Idea => ({
  id: "idea-1",
  projectId: "project-1",
  taskNumber: 1,
  title: "Design modal",
  summary: "",
  details: "",
  status: "idea",
  labels: [],
  acceptanceCriteria: [],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  createdAt: "2026-06-04T00:00:00.000Z",
  updatedAt: "2026-06-04T00:00:00.000Z",
  ...patch,
});

describe("task draft utilities", () => {
  it("creates an empty draft for the simplified task modal", () => {
    expect(emptyTaskDraft()).toEqual({
      title: "",
      description: "",
      labels: [],
      acceptanceCriteria: "",
      dependsOn: [],
      repositoryLocalPath: "",
      repositoryRemoteUrl: "",
      assignee: "",
      status: "idea",
    });
  });

  it("collapses existing summary and details into one editable description", () => {
    expect(
      taskToDraft(
        task({
          summary: "Short context",
          details: "Longer task description",
          status: "refining",
          labels: ["ui", "modal"],
          acceptanceCriteria: ["Labels are selectable", "Criteria is readable"],
          repositoryLocalPath: "/workspace/vibepod-board",
          repositoryRemoteUrl: "git@github.com:vibepod/vibepod-board.git",
        }),
      ),
    ).toMatchObject({
      id: "idea-1",
      title: "Design modal",
      description: "Short context\n\nLonger task description",
      labels: ["ui", "modal"],
      acceptanceCriteria: "Labels are selectable\nCriteria is readable",
      repositoryLocalPath: "/workspace/vibepod-board",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-board.git",
      status: "refining",
    });
  });

  it("stores the single modal description as task details", () => {
    expect(
      taskDraftToIdeaPayload({
        title: "Improve task modal",
        description: "One readable task description",
        labels: ["ui", "modal"],
        acceptanceCriteria:
          "Status and labels share a row\nCriteria uses a full width box",
        dependsOn: [],
        repositoryLocalPath: "/workspace/vibepod-board",
        repositoryRemoteUrl: "git@github.com:vibepod/vibepod-board.git",
        assignee: "Claude::Subagent101::Worktree12",
        status: "ready",
      }),
    ).toEqual({
      title: "Improve task modal",
      summary: "",
      details: "One readable task description",
      labels: ["ui", "modal"],
      acceptanceCriteria: [
        "Status and labels share a row",
        "Criteria uses a full width box",
      ],
      dependsOn: [],
      repositoryLocalPath: "/workspace/vibepod-board",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-board.git",
      assignee: "Claude::Subagent101::Worktree12",
    });
  });

  it("reads the holder back into the draft and treats none as empty", () => {
    expect(
      taskToDraft(task({ assignee: "Claude::Subagent101::Worktree12" }))
        .assignee,
    ).toBe("Claude::Subagent101::Worktree12");
    expect(taskToDraft(task()).assignee).toBe("");
  });
});
