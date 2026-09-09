import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { PostgresBoardStore } from "../src/server/storage.js";
import { adminAccess, tokenAccess } from "../src/server/store.js";
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

describe("reference resolution", () => {
  it("resolves a task by id, by key and by bare number in a scoped token", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Collapse the two-step write",
    });
    const scoped = tokenAccess("token-1", [vibepod.id]);

    const byId = await store.updateIdea(admin, task.id, { summary: "one" });
    const byKey = await store.updateIdea(admin, `VP-${task.taskNumber}`, {
      summary: "two",
    });
    const byLowerKey = await store.updateIdea(admin, `vp-${task.taskNumber}`, {
      summary: "three",
    });
    const byNumber = await store.updateIdea(scoped, String(task.taskNumber), {
      summary: "four",
    });

    expect([byId.id, byKey.id, byLowerKey.id, byNumber.id]).toEqual([
      task.id,
      task.id,
      task.id,
      task.id,
    ]);
    expect(byNumber.summary).toBe("four");
  });

  it("resolves a project by key and by title", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Only task",
    });

    expect(await store.listIdeas(admin, "VP")).toHaveLength(1);
    expect(await store.listIdeas(admin, "vp")).toHaveLength(1);
    expect(await store.listIdeas(admin, "VibePod")).toHaveLength(1);
    expect(await store.listIdeas(admin, "vibepod")).toHaveLength(1);
    expect(await store.listIdeas(admin, vibepod.id)).toHaveLength(1);
  });

  it("resolves the project of a new task by key", async () => {
    await store.createProject({ key: "VP", title: "VibePod" });

    const created = await store.createIdea(admin, {
      projectId: "VP",
      title: "Filed by key",
    });

    expect(created.taskNumber).toBe(1);
    expect(await store.listIdeas(admin, "VP")).toHaveLength(1);
  });

  it("refuses a bare task number when more than one project is in scope", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const other = await store.createProject({
      key: "OPS",
      title: "Operations",
    });
    await store.createIdea(admin, { projectId: vibepod.id, title: "First" });
    await store.createIdea(admin, { projectId: other.id, title: "Second" });

    await expect(
      store.updateIdea(admin, "1", { summary: "x" }),
    ).rejects.toThrow(
      "Ambiguous task reference: 1 matches 2 projects in scope",
    );
  });

  it("refuses an ambiguous project title", async () => {
    await store.createProject({ key: "BRD", title: "Board" });
    await store.createProject({ key: "BDX", title: "Board" });

    await expect(store.listIdeas(admin, "Board")).rejects.toThrow(
      "Ambiguous project reference: Board matches 2 projects (BDX, BRD)",
    );
  });

  it("reports a foreign task as not found rather than forbidden", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const other = await store.createProject({
      key: "OPS",
      title: "Operations",
    });
    const foreign = await store.createIdea(admin, {
      projectId: other.id,
      title: "Hidden",
    });
    const scoped = tokenAccess("token-1", [vibepod.id]);

    await expect(
      store.updateIdea(scoped, foreign.id, { summary: "x" }),
    ).rejects.toThrow(`Task not found: ${foreign.id}`);
    await expect(
      store.updateIdea(scoped, "VP-1", { summary: "x" }),
    ).rejects.toThrow("Task not found: VP-1");
  });

  it("names the project and number when a key does not resolve", async () => {
    await store.createProject({ key: "VP", title: "VibePod" });

    await expect(
      store.updateIdea(admin, "VP-999", { summary: "x" }),
    ).rejects.toThrow("Task not found: VP-999 (project VP, task 999)");
  });

  it("resolves a board card by task key", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "On the board",
    });
    await store.markIdeaReady(admin, task.id);

    const moved = await store.moveBoardCard(
      admin,
      `VP-${task.taskNumber}`,
      "planned",
    );

    expect(moved).toMatchObject({ ideaId: task.id, column: "planned" });
  });

  it("accepts keys as dependency arguments", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const blocker = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Blocker",
    });
    const waiting = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Waiting",
    });

    const updated = await store.setIdeaDependencies(
      admin,
      `VP-${waiting.taskNumber}`,
      [`VP-${blocker.taskNumber}`],
    );

    expect(updated.dependsOn).toEqual([blocker.id]);
  });

  it("still reports an unparseable dependency as not found", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Waiting",
    });

    await expect(
      store.setIdeaDependencies(admin, task.id, ["missing-task"]),
    ).rejects.toThrow("Task not found: missing-task");
  });
});
