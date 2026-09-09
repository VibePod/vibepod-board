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

const threeTasks = async () => {
  const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
  const tasks = [];
  for (const title of ["One", "Two", "Three"]) {
    tasks.push(await store.createIdea(admin, { projectId: vibepod.id, title }));
  }
  return { vibepod, tasks };
};

describe("batch writes", () => {
  it("applies every item and returns them in input order", async () => {
    const { tasks } = await threeTasks();

    const updated = await store.updateIdeas(admin, [
      { id: "VP-1", labels: ["api"] },
      { id: "VP-2", labels: ["ui"] },
      { id: "VP-3", labels: ["docs"] },
    ]);

    expect(updated.map((idea) => idea.id)).toEqual(tasks.map((t) => t.id));
    expect(updated.map((idea) => idea.labels)).toEqual([
      ["api"],
      ["ui"],
      ["docs"],
    ]);
  });

  it("rolls the whole batch back when one item fails", async () => {
    await threeTasks();

    await expect(
      store.updateIdeas(admin, [
        { id: "VP-1", labels: ["applied"] },
        { id: "VP-999", labels: ["missing"] },
        { id: "VP-3", labels: ["applied"] },
      ]),
    ).rejects.toThrow("Batch item 2 (VP-999) failed: Task not found: VP-999");

    expect((await store.getIdea(admin, "VP-1")).labels).toEqual([]);
  });

  it("rolls back on a stale item and names it", async () => {
    const { tasks } = await threeTasks();
    await store.updateIdea(admin, "VP-2", { summary: "Moved on" });

    await expect(
      store.updateIdeas(admin, [
        { id: "VP-1", labels: ["applied"] },
        {
          id: "VP-2",
          labels: ["stale"],
          expectedUpdatedAt: tasks[1].updatedAt,
        },
      ]),
    ).rejects.toThrow("Batch item 2 (VP-2) failed: Task VP-2 changed since");

    expect((await store.getIdea(admin, "VP-1")).labels).toEqual([]);
  });

  it("aborts when one item is out of scope", async () => {
    const { vibepod } = await threeTasks();
    const other = await store.createProject({
      key: "OPS",
      title: "Operations",
    });
    await store.createIdea(admin, { projectId: other.id, title: "Foreign" });
    const scoped = tokenAccess("token-1", [vibepod.id]);

    await expect(
      store.updateIdeas(scoped, [
        { id: "VP-1", labels: ["applied"] },
        { id: "OPS-1", labels: ["forbidden"] },
      ]),
    ).rejects.toThrow("Batch item 2 (OPS-1) failed: Task not found: OPS-1");

    expect((await store.getIdea(scoped, "VP-1")).labels).toEqual([]);
  });

  it("writes one activity row for the whole batch", async () => {
    await threeTasks();
    const before = (await store.listActivity(admin)).length;

    await store.updateIdeas(admin, [
      { id: "VP-1", labels: ["api"] },
      { id: "VP-2", labels: ["ui"] },
      { id: "VP-3", labels: ["docs"] },
    ]);

    const after = await store.listActivity(admin);
    expect(after.length).toBe(before + 1);
    expect(after[0]).toMatchObject({
      type: "idea.updated",
      message: "Updated 3 tasks",
    });
  });

  it("refuses more than fifty items", async () => {
    await threeTasks();

    await expect(
      store.updateIdeas(
        admin,
        Array.from({ length: 51 }, () => ({ id: "VP-1", labels: ["api"] })),
      ),
    ).rejects.toThrow("Batch is limited to 50 items; received 51");
  });

  it("moves several cards in one batch", async () => {
    const { vibepod, tasks } = await threeTasks();
    for (const task of tasks) {
      await store.markIdeaReady(admin, task.id);
    }

    const moved = await store.updateBoardCards(admin, [
      { id: "VP-1", column: "planned" },
      { id: "VP-2", column: "in_progress" },
    ]);

    expect(moved.map((card) => card.column)).toEqual([
      "planned",
      "in_progress",
    ]);
    const columns = await store.getBoardColumns(admin, vibepod.id);
    expect(columns.ready).toHaveLength(1);
  });
});
