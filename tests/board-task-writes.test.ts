import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { PostgresBoardStore } from "../src/server/storage.js";
import { adminAccess } from "../src/server/store.js";
import { closeTestPool, createTestStore } from "./helpers/store.js";

let pool: Pool;
let store: PostgresBoardStore;
const admin = adminAccess("admin");

beforeEach(async () => {
  const created = await createTestStore();
  pool = created.pool;
  store = created.store;
});

afterEach(async () => {
  await closeTestPool(pool);
});

const cardFor = async (projectId: string, ideaId: string) => {
  const cards = await store.listBoardCards(admin, projectId);
  return cards.find((card) => card.ideaId === ideaId);
};

describe("collapsed task writes", () => {
  it("creates the board card when a status edit reaches ready", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Becomes ready",
    });

    const updated = await store.updateIdea(admin, `VP-${task.taskNumber}`, {
      status: "ready",
      summary: "Scoped",
    });

    expect(updated.status).toBe("ready");
    expect(await cardFor(vibepod.id, task.id)).toMatchObject({
      ideaId: task.id,
      column: "ready",
    });
  });

  it("keeps the card when the status moves away from ready", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Goes back to refining",
    });
    await store.markIdeaReady(admin, task.id);
    const card = await cardFor(vibepod.id, task.id);
    await store.updateBoardCard(admin, card?.id ?? "", {
      column: "in_progress",
      branchName: "vp-1-work",
    });

    await store.updateIdea(admin, task.id, { status: "refining" });

    expect(await cardFor(vibepod.id, task.id)).toMatchObject({
      id: card?.id,
      column: "in_progress",
      branchName: "vp-1-work",
    });
  });

  it("removes the card only when onBoard is false", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Leaves the board",
    });
    await store.markIdeaReady(admin, task.id);

    await store.updateIdea(admin, task.id, { onBoard: false });

    expect(await cardFor(vibepod.id, task.id)).toBeUndefined();
  });

  it("puts a task on the board when onBoard is true", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Joins the board",
    });

    await store.updateIdea(admin, task.id, { onBoard: true });

    expect(await cardFor(vibepod.id, task.id)).toMatchObject({
      column: "ready",
    });
  });

  it("writes content and readiness in one call under one timestamp", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Rated while edited",
    });

    const updated = await store.updateIdea(admin, task.id, {
      details: "Refined scope",
      status: "ready",
      readiness: { score: 8, reason: "Scope and criteria are clear" },
    });

    expect(updated).toMatchObject({ readinessScore: 8, status: "ready" });
    // Equal timestamps mean "evaluated as of this edit", not "already stale".
    expect(updated.readinessEvaluatedAt).toBe(updated.updatedAt);
    expect(await store.listIdeaReadiness(admin, task.id)).toHaveLength(1);
    expect(await cardFor(vibepod.id, task.id)).toMatchObject({
      readinessScore: 8,
    });
  });

  it("accepts readiness alongside mark_idea_ready", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Ready and rated",
    });

    const ready = await store.markIdeaReady(admin, task.id, {
      readiness: { score: 6, reason: "Good enough to start" },
    });

    expect(ready).toMatchObject({ status: "ready", readinessScore: 6 });
    expect(await cardFor(vibepod.id, task.id)).toMatchObject({
      readinessScore: 6,
    });
  });

  it("writes one task activity row per task write", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Counted",
    });
    const before = await store.listActivity(admin);

    await store.updateIdea(admin, task.id, {
      status: "ready",
      readiness: { score: 7, reason: "Clear" },
    });

    const after = await store.listActivity(admin);
    const taskRows = (rows: typeof after) =>
      rows.filter((event) => event.type.startsWith("idea."));
    // Content, status and readiness collapse into one idea event; creating the
    // card is the board's own event, as it has always been.
    expect(taskRows(after).length).toBe(taskRows(before).length + 1);
    // Both rows share one timestamp, so assert membership rather than order.
    expect(after.map((event) => event.type)).toEqual(
      expect.arrayContaining(["idea.ready", "board.created"]),
    );
  });
});
