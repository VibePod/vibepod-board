import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createMcpToolHandlers } from "../src/server/mcpTools.js";
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

const projectWithTasks = async (titles: string[], key = "APP") => {
  const project = await store.createProject({ key, title: `Project ${key}` });
  const tasks = [];
  for (const title of titles) {
    tasks.push(await store.createIdea(admin, { projectId: project.id, title }));
  }
  return { project, tasks };
};

const finishTask = async (id: string) => {
  await store.markIdeaReady(admin, id);
  const cards = await store.listBoardCards(admin);
  const card = cards.find((item) => item.ideaId === id);
  if (!card) {
    throw new Error("Board card was not created");
  }
  return store.moveBoardCard(admin, card.id, "done");
};

describe("task dependencies", () => {
  it("links tasks and reports both directions of the graph", async () => {
    const { tasks } = await projectWithTasks(["Schema", "API"]);
    const [schema, api] = tasks;

    const linked = await store.addIdeaDependency(admin, api.id, schema.id);
    expect(linked.dependsOn).toEqual([schema.id]);
    expect(linked.blockedBy).toEqual([schema.id]);

    const reloaded = await store.listIdeas(admin);
    expect(reloaded.find((task) => task.id === schema.id)?.blocks).toEqual([
      api.id,
    ]);
    expect(reloaded.find((task) => task.id === schema.id)?.blockedBy).toEqual(
      [],
    );
  });

  it("clears blockedBy once the blocking task is done or denied", async () => {
    const { tasks } = await projectWithTasks(["Schema", "API", "Docs"]);
    const [schema, api, docs] = tasks;
    await store.setIdeaDependencies(admin, api.id, [schema.id, docs.id]);

    const blocked = await store.listIdeas(admin);
    expect(
      blocked.find((task) => task.id === api.id)?.blockedBy.sort(),
    ).toEqual([schema.id, docs.id].sort());

    await finishTask(schema.id);
    await store.updateIdea(admin, docs.id, { status: "denied" });

    const unblocked = await store.listIdeas(admin);
    expect(unblocked.find((task) => task.id === api.id)?.blockedBy).toEqual([]);
    expect(
      unblocked.find((task) => task.id === api.id)?.dependsOn.sort(),
    ).toEqual([schema.id, docs.id].sort());
  });

  it("mirrors dependencies onto the linked board card", async () => {
    const { tasks } = await projectWithTasks(["Schema", "API"]);
    const [schema, api] = tasks;
    await store.addIdeaDependency(admin, api.id, schema.id);
    await store.markIdeaReady(admin, api.id);

    const columns = await store.getBoardColumns(admin);
    const card = columns.ready.find((item) => item.ideaId === api.id);
    expect(card?.dependsOn).toEqual([schema.id]);
    expect(card?.blockedBy).toEqual([schema.id]);

    await finishTask(schema.id);
    const afterColumns = await store.getBoardColumns(admin);
    const afterCard = afterColumns.ready.find((item) => item.ideaId === api.id);
    expect(afterCard?.blockedBy).toEqual([]);
  });

  it("accepts dependencies when creating and updating a task", async () => {
    const { project, tasks } = await projectWithTasks(["Schema"]);
    const [schema] = tasks;

    const api = await store.createIdea(admin, {
      projectId: project.id,
      title: "API",
      dependsOn: [schema.id],
    });
    expect(api.dependsOn).toEqual([schema.id]);

    const cleared = await store.updateIdea(admin, api.id, { dependsOn: [] });
    expect(cleared.dependsOn).toEqual([]);

    const untouched = await store.updateIdea(admin, api.id, {
      title: "API v2",
    });
    expect(untouched.dependsOn).toEqual([]);
  });

  it("removes a single dependency edge", async () => {
    const { tasks } = await projectWithTasks(["Schema", "Docs", "API"]);
    const [schema, docs, api] = tasks;
    await store.setIdeaDependencies(admin, api.id, [schema.id, docs.id]);

    const remaining = await store.removeIdeaDependency(admin, api.id, docs.id);
    expect(remaining.dependsOn).toEqual([schema.id]);
  });

  it("rejects self dependencies, cycles, and cross-project blockers", async () => {
    const { tasks } = await projectWithTasks(["Schema", "API"]);
    const [schema, api] = tasks;
    const other = await projectWithTasks(["Outside"], "OPS");

    await expect(
      store.addIdeaDependency(admin, api.id, api.id),
    ).rejects.toThrow("A task cannot depend on itself");

    await expect(
      store.addIdeaDependency(admin, api.id, other.tasks[0].id),
    ).rejects.toThrow("Dependencies must stay in the same project");

    await store.addIdeaDependency(admin, api.id, schema.id);
    await expect(
      store.addIdeaDependency(admin, schema.id, api.id),
    ).rejects.toThrow("Dependency cycle detected");

    const unchanged = await store.listIdeas(admin);
    expect(unchanged.find((task) => task.id === schema.id)?.dependsOn).toEqual(
      [],
    );
  });

  it("rejects unknown blockers", async () => {
    const { tasks } = await projectWithTasks(["Schema"]);

    await expect(
      store.addIdeaDependency(admin, tasks[0].id, "missing-task"),
    ).rejects.toThrow("Task not found: missing-task");
  });

  it("keeps dependency writes inside the token project scope", async () => {
    const { project, tasks } = await projectWithTasks(["Schema", "API"]);
    const outside = await projectWithTasks(["Outside"], "OPS");
    const scoped = tokenAccess("token-1", [project.id]);

    await expect(
      store.addIdeaDependency(scoped, outside.tasks[0].id, tasks[0].id),
    ).rejects.toThrow("Token is not allowed to access project");

    const linked = await store.addIdeaDependency(
      scoped,
      tasks[1].id,
      tasks[0].id,
    );
    expect(linked.dependsOn).toEqual([tasks[0].id]);
  });

  it("returns a dependency-resolved work order", async () => {
    const { project, tasks } = await projectWithTasks([
      "Schema",
      "API",
      "Deploy",
    ]);
    const [schema, api, deploy] = tasks;
    await store.addIdeaDependency(admin, api.id, schema.id);
    await store.addIdeaDependency(admin, deploy.id, api.id);

    const workOrder = await store.getWorkOrder(admin, project.id);
    expect(workOrder.items.map((item) => item.id)).toEqual([
      schema.id,
      api.id,
      deploy.id,
    ]);
    expect(workOrder.items.map((item) => item.taskId)).toEqual([
      "APP-1",
      "APP-2",
      "APP-3",
    ]);
    expect(workOrder.items.map((item) => item.isActionable)).toEqual([
      true,
      false,
      false,
    ]);

    await finishTask(schema.id);
    const afterFirst = await store.getWorkOrder(admin, project.id);
    expect(afterFirst.items.map((item) => item.isActionable)).toEqual([
      false,
      true,
      false,
    ]);
    expect(afterFirst.items[0].isComplete).toBe(true);
    expect(afterFirst.items[1].cardId).toBeUndefined();
  });

  it("scopes the work order to the projects a token may read", async () => {
    const { project, tasks } = await projectWithTasks(["Schema", "API"]);
    const outside = await projectWithTasks(["Outside"], "OPS");
    const scoped = tokenAccess("token-1", [project.id]);

    const workOrder = await store.getWorkOrder(scoped);
    expect(workOrder.items.map((item) => item.id).sort()).toEqual(
      [tasks[0].id, tasks[1].id].sort(),
    );
    expect(workOrder.items.map((item) => item.id)).not.toContain(
      outside.tasks[0].id,
    );
  });

  it("exposes dependency tools over MCP", async () => {
    const { project, tasks } = await projectWithTasks(["Schema", "API"]);
    const [schema, api] = tasks;
    const handlers = createMcpToolHandlers(store, admin);

    const linked = await handlers.add_idea_dependency({
      id: api.id,
      dependsOnId: schema.id,
    });
    expect(linked.item.dependsOn).toEqual([schema.id]);

    const workOrder = await handlers.list_work_order({
      projectId: project.id,
    });
    expect(workOrder.items.map((item) => item.id)).toEqual([schema.id, api.id]);

    const replaced = await handlers.set_idea_dependencies({
      id: api.id,
      dependsOnIds: [],
    });
    expect(replaced.item.dependsOn).toEqual([]);

    await handlers.add_idea_dependency({ id: api.id, dependsOnId: schema.id });
    const removed = await handlers.remove_idea_dependency({
      id: api.id,
      dependsOnId: schema.id,
    });
    expect(removed.item.dependsOn).toEqual([]);
  });
});
