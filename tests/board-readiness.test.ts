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

describe("project readiness", () => {
  const seed = async () => {
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    const rated = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Rated",
    });
    const twice = await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Rated twice",
    });
    await store.createIdea(admin, {
      projectId: vibepod.id,
      title: "Never rated",
    });
    await store.setIdeaReadiness(admin, rated.id, {
      score: 5,
      reason: "Half there",
    });
    await store.setIdeaReadiness(admin, twice.id, {
      score: 3,
      reason: "Rough",
    });
    await store.setIdeaReadiness(admin, twice.id, {
      score: 9,
      reason: "Sharpened",
    });
    return { vibepod, rated, twice };
  };

  it("returns the latest readiness for every rated task in one call", async () => {
    const { rated, twice } = await seed();

    const latest = await store.listReadiness(admin, { projectId: "VP" });

    expect(latest).toHaveLength(2);
    expect(latest.find((event) => event.ideaId === twice.id)?.score).toBe(9);
    expect(latest.find((event) => event.ideaId === rated.id)?.score).toBe(5);
  });

  it("returns the full history when latestOnly is off", async () => {
    await seed();

    const all = await store.listReadiness(admin, {
      projectId: "VP",
      latestOnly: false,
    });

    expect(all).toHaveLength(3);
  });

  it("accepts a list of task references", async () => {
    const { twice } = await seed();

    const one = await store.listReadiness(admin, {
      tasks: [`VP-${twice.taskNumber}`],
    });

    expect(one).toHaveLength(1);
    expect(one[0]).toMatchObject({ ideaId: twice.id, score: 9 });
  });

  it("stays inside the caller's projects", async () => {
    const { vibepod } = await seed();
    const other = await store.createProject({
      key: "OPS",
      title: "Operations",
    });
    const foreign = await store.createIdea(admin, {
      projectId: other.id,
      title: "Hidden",
    });
    await store.setIdeaReadiness(admin, foreign.id, {
      score: 7,
      reason: "Not yours",
    });
    const scoped = tokenAccess("token-1", [vibepod.id]);

    const visible = await store.listReadiness(scoped, {});

    expect(visible).toHaveLength(2);
    expect(visible.every((event) => event.ideaId !== foreign.id)).toBe(true);
  });
});
