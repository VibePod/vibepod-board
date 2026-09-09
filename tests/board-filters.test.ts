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

const seed = async () => {
  const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
  const refining = await store.createIdea(admin, {
    projectId: vibepod.id,
    title: "Being refined",
  });
  // createIdea always inserts status "idea"; only updateIdea escalates.
  await store.updateIdea(admin, refining.id, {
    details: "Has detail so it escalates to refining",
  });
  const ready = await store.createIdea(admin, {
    projectId: vibepod.id,
    title: "Ready to go",
  });
  await store.markIdeaReady(admin, ready.id);
  const card = await store.getBoardCard(admin, ready.id);
  await store.updateBoardCard(admin, card.id, { column: "in_progress" });
  return { vibepod, refining, ready };
};

describe("list filters", () => {
  it("filters tasks by status", async () => {
    const { vibepod } = await seed();

    const refiningOnly = await store.listIdeas(admin, {
      projectId: vibepod.id,
      status: ["refining"],
    });
    const readyOnly = await store.listIdeas(admin, {
      projectId: vibepod.id,
      status: ["ready"],
    });
    const either = await store.listIdeas(admin, {
      projectId: vibepod.id,
      status: ["refining", "ready"],
    });

    expect(refiningOnly.map((idea) => idea.title)).toEqual(["Being refined"]);
    expect(readyOnly.map((idea) => idea.title)).toEqual(["Ready to go"]);
    expect(either).toHaveLength(2);
  });

  it("filters cards by column", async () => {
    const { vibepod } = await seed();

    const columns = await store.getBoardColumns(admin, {
      projectId: vibepod.id,
      column: ["in_progress"],
    });

    expect(columns.in_progress).toHaveLength(1);
    expect(columns.ready).toHaveLength(0);
  });

  it("returns only records changed after updatedSince", async () => {
    const { vibepod, refining } = await seed();
    const boundary = new Date(Date.now() + 5).toISOString();
    await new Promise((resolve) => setTimeout(resolve, 10));
    await store.updateIdea(admin, refining.id, { summary: "Touched" });

    const changed = await store.listIdeas(admin, {
      projectId: vibepod.id,
      updatedSince: boundary,
    });

    expect(changed.map((idea) => idea.id)).toEqual([refining.id]);
  });

  it("still accepts a bare project reference", async () => {
    await seed();

    expect(await store.listIdeas(admin, "VP")).toHaveLength(2);
    expect(await store.listIdeas(admin)).toHaveLength(2);
  });

  it("keeps returning nothing for a token with no projects", async () => {
    await seed();

    expect(await store.listIdeas(tokenAccess("token-1", []), {})).toEqual([]);
    expect(await store.listBoardCards(tokenAccess("token-1", []), {})).toEqual(
      [],
    );
  });

  it("scopes an unfiltered list to the token's projects", async () => {
    const { vibepod } = await seed();
    const other = await store.createProject({
      key: "OPS",
      title: "Operations",
    });
    await store.createIdea(admin, { projectId: other.id, title: "Hidden" });
    const scoped = tokenAccess("token-1", [vibepod.id]);

    const visible = await store.listIdeas(scoped, {});

    expect(visible).toHaveLength(2);
    expect(visible.every((idea) => idea.projectId === vibepod.id)).toBe(true);
  });
});
