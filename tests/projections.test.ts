import { describe, expect, it } from "vitest";

import {
  compactBoardCard,
  compactDocument,
  compactIdea,
  refBoardCard,
  refIdea,
} from "../src/shared/projections.js";
import type { BoardCard, Idea, PlanDocument } from "../src/shared/types.js";

const timestamp = "2026-09-03T06:00:00.000Z";

const idea = (): Idea => ({
  id: "idea-1",
  projectId: "project-1",
  taskNumber: 236,
  title: "Collapse the two-step write",
  summary: "Agents pay two round trips for one intent",
  details: "x".repeat(2048),
  status: "ready",
  labels: ["api"],
  acceptanceCriteria: ["One call sets status and readiness", "Card exists"],
  dependsOn: ["idea-2"],
  blocks: [],
  blockedBy: ["idea-2"],
  readinessScore: 8,
  readinessReason: "Scope is clear",
  readinessEvaluatedAt: timestamp,
  createdAt: timestamp,
  updatedAt: timestamp,
});

const card = (): BoardCard => ({
  id: "card-1",
  projectId: "project-1",
  ideaId: "idea-1",
  title: "Collapse the two-step write",
  details: "y".repeat(1024),
  column: "in_progress",
  branchName: "vp-236-collapse",
  labels: ["api"],
  dependsOn: ["idea-2"],
  blockedBy: ["idea-2"],
  readinessScore: 8,
  createdAt: timestamp,
  updatedAt: timestamp,
});

const document = (): PlanDocument => ({
  id: "document-1",
  projectId: "project-1",
  title: "Execution plan",
  kind: "execution_plan",
  content: "z".repeat(512),
  linkedIdeaIds: ["idea-1"],
  linkedCardIds: ["card-1"],
  createdAt: timestamp,
  updatedAt: timestamp,
});

const keys = new Map([
  ["idea-1", "VP-236"],
  ["idea-2", "VP-12"],
]);

describe("projections", () => {
  it("reduces an idea to its reference", () => {
    expect(refIdea(idea(), keys)).toEqual({
      id: "idea-1",
      key: "VP-236",
      updatedAt: timestamp,
    });
  });

  it("keeps state and relationships but no free text in a compact idea", () => {
    const compact = compactIdea(idea(), keys, "in_progress");

    expect(compact).toEqual({
      id: "idea-1",
      key: "VP-236",
      projectId: "project-1",
      taskNumber: 236,
      title: "Collapse the two-step write",
      status: "ready",
      labels: ["api"],
      column: "in_progress",
      readinessScore: 8,
      dependsOn: ["VP-12"],
      blockedBy: ["VP-12"],
      detailsLength: 2048,
      acceptanceCriteriaCount: 2,
      updatedAt: timestamp,
    });
    expect(JSON.stringify(compact)).not.toContain("Scope is clear");
    expect(JSON.stringify(compact)).not.toContain("xxxx");
  });

  it("names dependencies by key and falls back to the id when unknown", () => {
    const compact = compactIdea(idea(), new Map([["idea-1", "VP-236"]]));

    expect(compact.dependsOn).toEqual(["idea-2"]);
    expect(compact.column).toBeUndefined();
    expect("column" in compact).toBe(false);
  });

  it("projects a card with its task key", () => {
    expect(compactBoardCard(card(), keys)).toEqual({
      id: "card-1",
      key: "VP-236",
      ideaId: "idea-1",
      projectId: "project-1",
      title: "Collapse the two-step write",
      column: "in_progress",
      branchName: "vp-236-collapse",
      labels: ["api"],
      blockedBy: ["VP-12"],
      readinessScore: 8,
      detailsLength: 1024,
      updatedAt: timestamp,
    });
    expect(refBoardCard(card(), keys)).toEqual({
      id: "card-1",
      key: "VP-236",
      updatedAt: timestamp,
    });
  });

  it("carries the assignee into compact views and omits it when free", () => {
    const holder = "Claude::Subagent101::Worktree12";

    expect(compactIdea({ ...idea(), assignee: holder }, keys).assignee).toBe(
      holder,
    );
    expect(
      compactBoardCard({ ...card(), assignee: holder }, keys).assignee,
    ).toBe(holder);
    expect("assignee" in compactIdea(idea(), keys)).toBe(false);
    expect("assignee" in compactBoardCard(card(), keys)).toBe(false);
  });

  it("drops document content and keeps its length", () => {
    const compact = compactDocument(document());

    expect(compact).toEqual({
      id: "document-1",
      projectId: "project-1",
      title: "Execution plan",
      kind: "execution_plan",
      linkedIdeaIds: ["idea-1"],
      contentLength: 512,
      updatedAt: timestamp,
    });
    expect(JSON.stringify(compact)).not.toContain("zzzz");
  });
});
