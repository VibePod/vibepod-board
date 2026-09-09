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

const task = async (title: string) => {
  const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
  return {
    vibepod,
    task: await store.createIdea(admin, { projectId: vibepod.id, title }),
  };
};

describe("optimistic concurrency", () => {
  it("accepts a current timestamp and rejects a stale one", async () => {
    const { task: contended } = await task("Contended");

    const first = await store.updateIdea(admin, contended.id, {
      summary: "First writer",
      expectedUpdatedAt: contended.updatedAt,
    });

    await expect(
      store.updateIdea(admin, contended.id, {
        summary: "Second writer",
        expectedUpdatedAt: contended.updatedAt,
      }),
    ).rejects.toThrow(
      `Task VP-${contended.taskNumber} changed since ${contended.updatedAt}`,
    );

    const fresh = await store.getIdea(admin, contended.id);
    expect(fresh.summary).toBe("First writer");
    expect(first.updatedAt).toBe(fresh.updatedAt);
  });

  it("reports the current timestamp in the conflict message", async () => {
    const { task: contended } = await task("Contended");
    const moved = await store.updateIdea(admin, contended.id, {
      summary: "Moved on",
    });

    await expect(
      store.updateIdea(admin, contended.id, {
        summary: "Stale",
        expectedUpdatedAt: contended.updatedAt,
      }),
    ).rejects.toThrow(`current updatedAt ${moved.updatedAt}`);
  });

  it("guards card writes too", async () => {
    const { vibepod, task: onBoard } = await task("On the board");
    await store.markIdeaReady(admin, onBoard.id);
    const card = await store.getBoardCard(admin, onBoard.id);
    await store.updateBoardCard(admin, card.id, { column: "planned" });

    await expect(
      store.updateBoardCard(admin, card.id, {
        column: "review",
        expectedUpdatedAt: card.updatedAt,
      }),
    ).rejects.toThrow("changed since");

    const columns = await store.getBoardColumns(admin, vibepod.id);
    expect(columns.planned).toHaveLength(1);
  });

  it("bumps updatedAt on a dependency write so a guard can see it", async () => {
    const { vibepod } = await task("Ignored");
    const blocker = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Blocker",
    });
    const waiting = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Waiting",
    });

    const linked = await store.setIdeaDependencies(admin, waiting.id, [
      blocker.id,
    ]);

    expect(linked.updatedAt).not.toBe(waiting.updatedAt);
    await expect(
      store.updateIdea(admin, waiting.id, {
        summary: "Stale",
        expectedUpdatedAt: waiting.updatedAt,
      }),
    ).rejects.toThrow("changed since");
  });

  it("leaves updatedAt alone on a readiness write", async () => {
    const { task: rated } = await task("Rated");

    const scored = await store.setIdeaReadiness(admin, rated.id, {
      score: 7,
      reason: "Clear enough",
    });

    // The staleness badge compares content time against evaluation time, so a
    // readiness write must not age the record.
    expect(scored.updatedAt).toBe(rated.updatedAt);
    await store.updateIdea(admin, rated.id, {
      summary: "Still writable",
      expectedUpdatedAt: rated.updatedAt,
    });
  });

  it("applies a write with no guard exactly as before", async () => {
    const { task: open } = await task("Unguarded");
    await store.updateIdea(admin, open.id, { summary: "One" });

    const second = await store.updateIdea(admin, open.id, { summary: "Two" });

    expect(second.summary).toBe("Two");
  });
});
