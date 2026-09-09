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

describe("single record reads", () => {
  it("returns a decorated task with its dependency arrays populated", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const blocker = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Blocker",
    });
    const waiting = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Waiting",
      dependsOn: [blocker.id],
    });

    const fetched = await store.getIdea(admin, `VP-${waiting.taskNumber}`);

    expect(fetched).toMatchObject({
      id: waiting.id,
      title: "Waiting",
      dependsOn: [blocker.id],
      blockedBy: [blocker.id],
    });
    expect((await store.getIdea(admin, blocker.id)).blocks).toEqual([
      waiting.id,
    ]);
  });

  it("returns a decorated card by task key", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "On the board",
    });
    await store.markIdeaReady(admin, task.id);

    const card = await store.getBoardCard(admin, `VP-${task.taskNumber}`);

    expect(card).toMatchObject({ ideaId: task.id, column: "ready" });
  });

  it("refuses a record outside the caller's projects", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const other = await store.createProject({
      key: "OPS",
      title: "Operations",
    });
    const foreign = await store.createIdea(admin, {
      projectId: other.id,
      title: "Hidden",
    });
    await store.markIdeaReady(admin, foreign.id);
    const scoped = tokenAccess("token-1", [vibepod.id]);

    await expect(store.getIdea(scoped, foreign.id)).rejects.toThrow(
      `Task not found: ${foreign.id}`,
    );
    await expect(store.getBoardCard(scoped, "OPS-1")).rejects.toThrow(
      "Board card not found: OPS-1",
    );
  });

  it("reports an unresolvable reference", async () => {
    await store.createProject({ key: "VP", title: "VibePod" });

    await expect(store.getIdea(admin, "VP-999")).rejects.toThrow(
      "Task not found: VP-999 (project VP, task 999)",
    );
  });
});
