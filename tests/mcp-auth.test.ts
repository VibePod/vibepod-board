import type { Pool } from "pg";
import request from "supertest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createApp } from "../src/server/app.js";
import { createAdminSessionManager } from "../src/server/auth.js";
import { createMcpToolHandlers } from "../src/server/mcpTools.js";
import type { PostgresBoardStore } from "../src/server/storage.js";
import { adminAccess, tokenAccess } from "../src/server/store.js";
import { closeTestPool, createTestStore } from "./helpers/store.js";

let pool: Pool;
let store: PostgresBoardStore;

beforeEach(async () => {
  const created = await createTestStore();
  pool = created.pool;
  store = created.store;
});

afterEach(async () => {
  await closeTestPool(pool);
});

describe("MCP auth", () => {
  it("requires a bearer token for the MCP route", async () => {
    const sessions = createAdminSessionManager({
      username: "admin",
      password: "secret",
    });
    const app = createApp({ store, sessions });

    await request(app)
      .post("/mcp")
      .send({ jsonrpc: "2.0", id: 1, method: "tools/list" })
      .expect(401);
    await request(app)
      .post("/mcp")
      .set("Authorization", "Bearer invalid")
      .send({ jsonrpc: "2.0", id: 1, method: "tools/list" })
      .expect(401);
  });

  it("scopes MCP tool handlers to mapped projects", async () => {
    const appProject = await store.createProject({ key: "APP", title: "App" });
    const apiProject = await store.createProject({ key: "API", title: "API" });
    await store.createIdea(adminAccess("admin"), {
      projectId: appProject.id,
      title: "Visible",
    });
    await store.createIdea(adminAccess("admin"), {
      projectId: apiProject.id,
      title: "Hidden",
    });

    const handlers = createMcpToolHandlers(
      store,
      tokenAccess("token-1", [appProject.id]),
    );

    await expect(
      handlers.create_project({ key: "NEW", title: "Nope" }),
    ).rejects.toThrow("Admin access required");
    await expect(
      handlers.create_idea({ projectId: apiProject.id, title: "Forbidden" }),
    ).rejects.toThrow("Token is not allowed to access project");

    const listed = await handlers.list_ideas({});
    expect(listed).toEqual({
      items: [expect.objectContaining({ title: "Visible" })],
    });
  });

  it("edits ideas in mapped projects but rejects edits to others", async () => {
    const appProject = await store.createProject({ key: "APP", title: "App" });
    const apiProject = await store.createProject({ key: "API", title: "API" });
    const mine = await store.createIdea(adminAccess("admin"), {
      projectId: appProject.id,
      title: "Editable",
    });
    const theirs = await store.createIdea(adminAccess("admin"), {
      projectId: apiProject.id,
      title: "Off limits",
    });

    const handlers = createMcpToolHandlers(
      store,
      tokenAccess("token-1", [appProject.id]),
    );

    const updated = await handlers.update_idea({
      id: mine.id,
      title: "Edited title",
      labels: ["enhancement"],
    });
    expect(updated.item).toMatchObject({
      title: "Edited title",
      labels: ["enhancement"],
    });

    // A record outside the token's projects is reported as missing, not as
    // forbidden, so scope cannot be probed by comparing the two answers.
    await expect(
      handlers.update_idea({ id: theirs.id, title: "Nope" }),
    ).rejects.toThrow(`Task not found: ${theirs.id}`);
  });

  it("updates board card branch names in mapped projects", async () => {
    const appProject = await store.createProject({ key: "APP", title: "App" });
    const apiProject = await store.createProject({ key: "API", title: "API" });
    const mine = await store.createIdea(adminAccess("admin"), {
      projectId: appProject.id,
      title: "Editable card",
    });
    const theirs = await store.createIdea(adminAccess("admin"), {
      projectId: apiProject.id,
      title: "Off limits card",
    });
    await store.markIdeaReady(adminAccess("admin"), mine.id);
    await store.markIdeaReady(adminAccess("admin"), theirs.id);
    const mineCard = (
      await store.getBoardColumns(adminAccess("admin"), appProject.id)
    ).ready[0];
    const theirCard = (
      await store.getBoardColumns(adminAccess("admin"), apiProject.id)
    ).ready[0];

    const handlers = createMcpToolHandlers(
      store,
      tokenAccess("token-1", [appProject.id]),
    );

    // Write echoes are compact now; ask for the whole record to assert on it.
    const updated = await handlers.update_board_card({
      id: mineCard.id,
      branchName: "vp-task-create",
      details: "Implemented in vibepod-cli",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git",
      view: "full",
    });
    expect(updated.item).toMatchObject({
      id: mineCard.id,
      branchName: "vp-task-create",
      details: "Implemented in vibepod-cli",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git",
    });

    await expect(
      handlers.update_board_card({
        id: theirCard.id,
        branchName: "vp-task-cancel",
      }),
    ).rejects.toThrow(`Board card not found: ${theirCard.id}`);
  });

  it("sets card readiness in mapped projects only", async () => {
    const appProject = await store.createProject({ key: "APP", title: "App" });
    const apiProject = await store.createProject({ key: "API", title: "API" });
    const mine = await store.createIdea(adminAccess("admin"), {
      projectId: appProject.id,
      title: "Scored card",
    });
    const theirs = await store.createIdea(adminAccess("admin"), {
      projectId: apiProject.id,
      title: "Off limits card",
    });
    await store.markIdeaReady(adminAccess("admin"), mine.id);
    await store.markIdeaReady(adminAccess("admin"), theirs.id);
    const mineCard = (
      await store.getBoardColumns(adminAccess("admin"), appProject.id)
    ).ready[0];
    const theirCard = (
      await store.getBoardColumns(adminAccess("admin"), apiProject.id)
    ).ready[0];

    const handlers = createMcpToolHandlers(
      store,
      tokenAccess("token-1", [appProject.id]),
    );

    const scored = await handlers.set_card_readiness({
      id: mineCard.id,
      score: 3,
      reason: "No acceptance criteria, repository unset",
      view: "full",
    });
    expect(scored.item).toMatchObject({
      id: mineCard.id,
      readinessScore: 3,
      readinessReason: "No acceptance criteria, repository unset",
    });

    await expect(
      handlers.set_card_readiness({ id: theirCard.id, score: 5, reason: "r" }),
    ).rejects.toThrow(`Board card not found: ${theirCard.id}`);
  });

  it("returns compact tasks from list_ideas by default and full on request", async () => {
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Compact by default",
      details: "x".repeat(2048),
      acceptanceCriteria: ["One", "Two"],
    });
    const handlers = createMcpToolHandlers(
      store,
      tokenAccess("token-1", [project.id]),
    );

    const compact = await handlers.list_ideas({});
    expect(compact.items[0]).toEqual({
      id: task.id,
      key: `VP-${task.taskNumber}`,
      projectId: project.id,
      taskNumber: task.taskNumber,
      title: "Compact by default",
      status: "idea",
      labels: [],
      dependsOn: [],
      blockedBy: [],
      detailsLength: 2048,
      acceptanceCriteriaCount: 2,
      updatedAt: expect.any(String),
    });

    const full = await handlers.list_ideas({ view: "full" });
    expect((full.items[0] as { details: string }).details).toHaveLength(2048);

    const one = await handlers.get_idea({ idea: `VP-${task.taskNumber}` });
    expect((one.item as { details: string }).details).toHaveLength(2048);
    expect((one.item as { key: string }).key).toBe(`VP-${task.taskNumber}`);
  });

  it("names a compact task's dependencies and column by key", async () => {
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const blocker = await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Blocker",
    });
    const waiting = await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Waiting",
      dependsOn: [blocker.id],
    });
    await store.markIdeaReady(adminAccess("admin"), waiting.id);
    const handlers = createMcpToolHandlers(
      store,
      tokenAccess("token-1", [project.id]),
    );

    const compact = await handlers.list_ideas({});
    const item = compact.items.find(
      (entry) => (entry as { id: string }).id === waiting.id,
    ) as { dependsOn: string[]; blockedBy: string[]; column?: string };

    expect(item.dependsOn).toEqual([`VP-${blocker.taskNumber}`]);
    expect(item.blockedBy).toEqual([`VP-${blocker.taskNumber}`]);
    expect(item.column).toBe("ready");
  });

  it("claims a task and finds held and free work through MCP", async () => {
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const holder = "Claude::Subagent101::Worktree12";
    const handlers = createMcpToolHandlers(
      store,
      tokenAccess("token-1", [project.id]),
    );
    const mine = await handlers.create_idea({
      title: "Mine",
      assignee: holder,
    });
    await handlers.create_idea({ title: "Free" });

    const held = await handlers.list_ideas({ assignee: [holder] });
    expect(held.items).toEqual([
      expect.objectContaining({ title: "Mine", assignee: holder }),
    ]);

    const free = await handlers.list_ideas({ unassigned: true });
    expect(free.items).toEqual([expect.objectContaining({ title: "Free" })]);
    expect("assignee" in (free.items[0] as object)).toBe(false);

    const released = await handlers.update_idea({
      id: (mine.item as { id: string }).id,
      assignee: "",
      view: "full",
    });
    expect((released.item as { assignee?: string }).assignee).toBeUndefined();
  });

  it("echoes a compact task from a write and a bare ref on request", async () => {
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Echo",
      details: "x".repeat(4096),
    });
    const handlers = createMcpToolHandlers(
      store,
      tokenAccess("token-1", [project.id]),
    );

    const compact = await handlers.update_idea({
      id: `VP-${task.taskNumber}`,
      summary: "Edited",
    });
    expect((compact.item as { details?: string }).details).toBeUndefined();
    expect((compact.item as { key: string }).key).toBe(`VP-${task.taskNumber}`);
    expect(JSON.stringify(compact).length).toBeLessThan(500);

    const asRef = await handlers.update_idea({
      id: `VP-${task.taskNumber}`,
      summary: "Edited again",
      view: "ref",
    });
    expect(asRef.item).toEqual({
      id: task.id,
      key: `VP-${task.taskNumber}`,
      updatedAt: expect.any(String),
    });

    const full = await handlers.update_idea({
      id: task.id,
      summary: "Edited once more",
      view: "full",
    });
    expect((full.item as { details: string }).details).toHaveLength(4096);
  });
});
