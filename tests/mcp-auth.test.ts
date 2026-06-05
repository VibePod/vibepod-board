import type { Pool } from "pg";
import request from "supertest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createAdminSessionManager } from "../src/server/auth.js";
import { createApp } from "../src/server/app.js";
import { createMcpToolHandlers } from "../src/server/mcpTools.js";
import { adminAccess, tokenAccess } from "../src/server/store.js";
import type { PostgresBoardStore } from "../src/server/storage.js";
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
    const sessions = createAdminSessionManager({ username: "admin", password: "secret" });
    const app = createApp({ store, sessions });

    await request(app).post("/mcp").send({ jsonrpc: "2.0", id: 1, method: "tools/list" }).expect(401);
    await request(app)
      .post("/mcp")
      .set("Authorization", "Bearer invalid")
      .send({ jsonrpc: "2.0", id: 1, method: "tools/list" })
      .expect(401);
  });

  it("scopes MCP tool handlers to mapped projects", async () => {
    const appProject = await store.createProject({ key: "APP", title: "App" });
    const apiProject = await store.createProject({ key: "API", title: "API" });
    await store.createIdea(adminAccess("admin"), { projectId: appProject.id, title: "Visible" });
    await store.createIdea(adminAccess("admin"), { projectId: apiProject.id, title: "Hidden" });

    const handlers = createMcpToolHandlers(store, tokenAccess("token-1", [appProject.id]));

    await expect(handlers.create_project({ key: "NEW", title: "Nope" })).rejects.toThrow(
      "Admin access required"
    );
    await expect(handlers.create_idea({ projectId: apiProject.id, title: "Forbidden" })).rejects.toThrow(
      "Token is not allowed to access project"
    );

    const listed = await handlers.list_ideas({});
    expect(listed).toEqual({ items: [expect.objectContaining({ title: "Visible" })] });
  });
});
