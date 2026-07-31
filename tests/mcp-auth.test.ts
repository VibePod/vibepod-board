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

    await expect(
      handlers.update_idea({ id: theirs.id, title: "Nope" }),
    ).rejects.toThrow("Token is not allowed to access project");
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

    const updated = await handlers.update_board_card({
      id: mineCard.id,
      branchName: "vp-task-create",
      details: "Implemented in vibepod-cli",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git",
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
    ).rejects.toThrow("Token is not allowed to access project");
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
    });
    expect(scored.item).toMatchObject({
      id: mineCard.id,
      readinessScore: 3,
      readinessReason: "No acceptance criteria, repository unset",
    });

    await expect(
      handlers.set_card_readiness({ id: theirCard.id, score: 5, reason: "r" }),
    ).rejects.toThrow("Token is not allowed to access project");
  });
});
