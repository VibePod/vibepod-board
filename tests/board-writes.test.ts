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

const readyTask = async (title: string) => {
  const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
  const task = await store.createIdea(admin, { projectId: vibepod.id, title });
  await store.markIdeaReady(admin, task.id);
  const cards = await store.listBoardCards(admin, vibepod.id);
  const card = cards.find((entry) => entry.ideaId === task.id);
  if (!card) {
    throw new Error("expected a board card for the ready task");
  }
  return { vibepod, task, card };
};

describe("card writes", () => {
  it("records a move when the column changes and an update otherwise", async () => {
    const { card } = await readyTask("Moves and edits");

    await store.updateBoardCard(admin, card.id, {
      column: "in_progress",
      branchName: "vp-1-work",
    });
    const afterMove = await store.listActivity(admin);
    expect(afterMove[0]).toMatchObject({
      type: "board.moved",
      message: "Moved card to in_progress: Moves and edits",
    });

    await store.updateBoardCard(admin, card.id, { branchName: "vp-1-retry" });
    const afterEdit = await store.listActivity(admin);
    expect(afterEdit[0]).toMatchObject({
      type: "board.updated",
      message: "Updated board card: Moves and edits",
    });
  });

  it("records an update when the column is sent unchanged", async () => {
    const { card } = await readyTask("Stays put");

    await store.updateBoardCard(admin, card.id, { column: "ready" });

    expect((await store.listActivity(admin))[0]).toMatchObject({
      type: "board.updated",
    });
  });

  it("moves a card by task key in one call", async () => {
    const { task } = await readyTask("Keyed move");

    const moved = await store.updateBoardCard(admin, `VP-${task.taskNumber}`, {
      column: "review",
    });

    expect(moved.column).toBe("review");
  });

  it("writes exactly one activity row per card write", async () => {
    const { card } = await readyTask("Counted");
    const before = (await store.listActivity(admin)).length;

    await store.updateBoardCard(admin, card.id, { column: "planned" });

    expect((await store.listActivity(admin)).length).toBe(before + 1);
  });
});
