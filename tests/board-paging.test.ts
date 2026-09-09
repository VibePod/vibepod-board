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

describe("keyset paging", () => {
  it("pages through a tie in updatedAt without repeating or skipping", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    for (const title of ["A", "B", "C", "D", "E"]) {
      await store.createIdea(admin, { projectId: vibepod.id, title });
    }
    // One write stamps several rows with one timestamp, so ties are routine.
    await pool.query("update ideas set updated_at = $1", [
      "2026-09-03T06:00:00.000Z",
    ]);

    const first = await store.listIdeasPage(admin, {
      projectId: "VP",
      limit: 2,
    });
    const second = await store.listIdeasPage(admin, {
      projectId: "VP",
      limit: 2,
      cursor: first.nextCursor,
    });
    const third = await store.listIdeasPage(admin, {
      projectId: "VP",
      limit: 2,
      cursor: second.nextCursor,
    });

    const seen = [...first.items, ...second.items, ...third.items].map(
      (idea) => idea.id,
    );
    expect(seen).toHaveLength(5);
    expect(new Set(seen).size).toBe(5);
    expect(third.nextCursor).toBeUndefined();
  });

  it("returns everything when no limit is given", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    for (const title of ["A", "B", "C"]) {
      await store.createIdea(admin, { projectId: vibepod.id, title });
    }

    const page = await store.listIdeasPage(admin, { projectId: "VP" });

    expect(page.items).toHaveLength(3);
    expect(page.nextCursor).toBeUndefined();
  });

  it("pages board cards too", async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    for (const title of ["A", "B", "C"]) {
      const task = await store.createIdea(admin, {
        projectId: vibepod.id,
        title,
      });
      await store.markIdeaReady(admin, task.id);
    }

    const first = await store.listBoardCardsPage(admin, {
      projectId: "VP",
      limit: 2,
    });
    const second = await store.listBoardCardsPage(admin, {
      projectId: "VP",
      limit: 2,
      cursor: first.nextCursor,
    });

    expect(first.items).toHaveLength(2);
    expect(second.items).toHaveLength(1);
    expect(second.nextCursor).toBeUndefined();
  });
});
