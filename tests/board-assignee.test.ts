import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { PostgresBoardStore } from "../src/server/storage.js";
import { adminAccess } from "../src/server/store.js";
import { closeTestPool, createTestStore } from "./helpers/store.js";

let pool: Pool;
let store: PostgresBoardStore;
const admin = adminAccess("admin");
const agent = "Claude::Subagent101::Worktree12";

beforeEach(async () => {
  const created = await createTestStore();
  pool = created.pool;
  store = created.store;
});

afterEach(async () => {
  await closeTestPool(pool);
});

const seedProject = async () =>
  store.createProject({ key: "VP", title: "VibePod" });

const readyTask = async (projectId: string, title: string) => {
  const task = await store.createIdea(admin, { projectId, title });
  await store.markIdeaReady(admin, task.id);
  return task;
};

describe("task assignees", () => {
  it("assigns a task and mirrors the holder onto its card", async () => {
    const project = await seedProject();
    const task = await readyTask(project.id, "Needs an owner");

    const updated = await store.updateIdea(admin, task.id, { assignee: agent });

    expect(updated.assignee).toBe(agent);
    expect((await store.getBoardCard(admin, task.id)).assignee).toBe(agent);
  });

  it("carries the holder onto a card created later", async () => {
    const project = await seedProject();
    const task = await store.createIdea(admin, {
      projectId: project.id,
      title: "Claimed before the board",
      assignee: agent,
    });

    await store.markIdeaReady(admin, task.id);

    expect((await store.getBoardCard(admin, task.id)).assignee).toBe(agent);
  });

  it("releases the task when the assignee is cleared", async () => {
    const project = await seedProject();
    const task = await readyTask(project.id, "Handed back");
    await store.updateIdea(admin, task.id, { assignee: agent });

    const released = await store.updateIdea(admin, task.id, { assignee: "" });

    expect(released.assignee).toBeUndefined();
    expect((await store.getBoardCard(admin, task.id)).assignee).toBeUndefined();
  });

  it("writes a claim made on the card through to its task", async () => {
    const project = await seedProject();
    const task = await readyTask(project.id, "Claimed from the board");
    const card = await store.getBoardCard(admin, task.id);

    await store.updateBoardCard(admin, card.id, { assignee: agent });

    expect((await store.getIdea(admin, task.id)).assignee).toBe(agent);
  });

  it("survives a later task edit rather than being synced away", async () => {
    const project = await seedProject();
    const task = await readyTask(project.id, "Edited after the claim");
    const card = await store.getBoardCard(admin, task.id);
    await store.updateBoardCard(admin, card.id, { assignee: agent });

    await store.updateIdea(admin, task.id, {
      title: "Renamed by someone else",
    });

    expect((await store.getBoardCard(admin, task.id)).assignee).toBe(agent);
  });

  it("leaves the holder alone when a card write omits it", async () => {
    const project = await seedProject();
    const task = await readyTask(project.id, "Moved, not reassigned");
    await store.updateIdea(admin, task.id, { assignee: agent });
    const card = await store.getBoardCard(admin, task.id);

    await store.updateBoardCard(admin, card.id, { column: "in_progress" });

    expect((await store.getBoardCard(admin, task.id)).assignee).toBe(agent);
    expect((await store.getIdea(admin, task.id)).assignee).toBe(agent);
  });

  it("refuses a competing claim guarded by expectedUpdatedAt", async () => {
    const project = await seedProject();
    const task = await readyTask(project.id, "Contended");
    const read = await store.getIdea(admin, task.id);
    await store.updateIdea(admin, task.id, { assignee: agent });

    await expect(
      store.updateIdea(admin, task.id, {
        assignee: "Claude::Subagent202::Worktree7",
        expectedUpdatedAt: read.updatedAt,
      }),
    ).rejects.toThrow(/changed since/);
    expect((await store.getIdea(admin, task.id)).assignee).toBe(agent);
  });

  it("trims surrounding whitespace", async () => {
    const project = await seedProject();
    const task = await readyTask(project.id, "Padded");

    const updated = await store.updateIdea(admin, task.id, {
      assignee: `  ${agent}  `,
    });

    expect(updated.assignee).toBe(agent);
  });
});

describe("assignee filters", () => {
  const other = "Codex::Session3";

  const seedHolders = async () => {
    const project = await seedProject();
    const mine = await store.createIdea(admin, {
      projectId: project.id,
      title: "Mine",
      assignee: agent,
    });
    const theirs = await store.createIdea(admin, {
      projectId: project.id,
      title: "Theirs",
      assignee: other,
    });
    const free = await store.createIdea(admin, {
      projectId: project.id,
      title: "Free",
    });
    for (const task of [mine, theirs, free]) {
      await store.markIdeaReady(admin, task.id);
    }
    return { project, mine, theirs, free };
  };

  it("lists only the named holders' tasks", async () => {
    const { project } = await seedHolders();

    const items = await store.listIdeas(admin, {
      projectId: project.id,
      assignee: [agent],
    });

    expect(items.map((idea) => idea.title)).toEqual(["Mine"]);
  });

  it("lists tasks nobody holds", async () => {
    const { project } = await seedHolders();

    const items = await store.listIdeas(admin, {
      projectId: project.id,
      unassigned: true,
    });

    expect(items.map((idea) => idea.title)).toEqual(["Free"]);
  });

  it("widens rather than narrows when both are given", async () => {
    const { project } = await seedHolders();

    const items = await store.listIdeas(admin, {
      projectId: project.id,
      assignee: [agent],
      unassigned: true,
    });

    expect(items.map((idea) => idea.title).sort()).toEqual(["Free", "Mine"]);
  });

  it("returns every task when neither is given", async () => {
    const { project } = await seedHolders();

    const items = await store.listIdeas(admin, { projectId: project.id });

    expect(items).toHaveLength(3);
  });

  it("filters board cards by holder", async () => {
    const { project } = await seedHolders();

    const held = await store.listBoardCards(admin, {
      projectId: project.id,
      assignee: [other],
    });
    const free = await store.listBoardCards(admin, {
      projectId: project.id,
      unassigned: true,
    });

    expect(held.map((card) => card.title)).toEqual(["Theirs"]);
    expect(free.map((card) => card.title)).toEqual(["Free"]);
  });

  it("filters a paged task list", async () => {
    const { project } = await seedHolders();

    const page = await store.listIdeasPage(admin, {
      projectId: project.id,
      assignee: [agent, other],
      limit: 10,
    });

    expect(page.items.map((idea) => idea.title).sort()).toEqual([
      "Mine",
      "Theirs",
    ]);
  });

  it("filters a paged card list", async () => {
    const { project } = await seedHolders();

    const page = await store.listBoardCardsPage(admin, {
      projectId: project.id,
      unassigned: true,
      limit: 10,
    });

    expect(page.items.map((card) => card.title)).toEqual(["Free"]);
  });
});
