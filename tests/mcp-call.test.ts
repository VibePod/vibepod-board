import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Pool } from "pg";
import request from "supertest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createApp } from "../src/server/app.js";
import { createAdminSessionManager } from "../src/server/auth.js";
import { mcpToolNames } from "../src/server/mcp.js";
import type { PostgresBoardStore } from "../src/server/storage.js";
import { adminAccess } from "../src/server/store.js";
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

const authedApp = () => {
  const sessions = createAdminSessionManager({
    username: "admin",
    password: "secret",
  });
  return createApp({ store, sessions });
};

/** The transport answers with an SSE stream, not a JSON body. */
const dataFrame = (body: string) => {
  const line = body.split("\n").find((entry) => entry.startsWith("data:"));
  if (!line) {
    throw new Error(`No data frame in response: ${body}`);
  }
  return JSON.parse(line.slice(5).trim());
};

const callTool = async (
  app: ReturnType<typeof createApp>,
  token: string,
  name: string,
  args: Record<string, unknown>,
) => {
  const response = await request(app)
    .post("/mcp")
    .set("Authorization", `Bearer ${token}`)
    .set("Accept", "application/json, text/event-stream")
    .set("Content-Type", "application/json")
    .send({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name, arguments: args },
    })
    .expect(200);
  return dataFrame(response.text).result;
};

describe("MCP tools/call", () => {
  it("reads tasks by key over the wire and returns compact JSON", async () => {
    const app = authedApp();
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Over the wire",
      details: "Body that compact must omit",
    });
    const token = await store.createApiToken({
      name: "Agent",
      projectIds: [project.id],
    });

    const result = await callTool(app, token.token, "list_ideas", {
      projectId: "VP",
    });

    expect(result.isError).toBeUndefined();
    const text = result.content[0].text as string;
    // Compact serialisation: no pretty-print indentation on the wire.
    expect(text).not.toContain("\n  ");
    const items = JSON.parse(text).items;
    expect(items[0]).toMatchObject({
      id: task.id,
      key: `VP-${task.taskNumber}`,
      detailsLength: 27,
    });
    expect(items[0].details).toBeUndefined();
  });

  it("surfaces an unresolvable reference as a tool error", async () => {
    const app = authedApp();
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const token = await store.createApiToken({
      name: "Agent",
      projectIds: [project.id],
    });

    const result = await callTool(app, token.token, "get_idea", {
      idea: "VP-999",
    });

    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain(
      "Task not found: VP-999 (project VP, task 999)",
    );
  });

  it("accepts a parameter that a rebuilt argument object would have dropped", async () => {
    const app = authedApp();
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Ready and rated",
    });
    const token = await store.createApiToken({
      name: "Agent",
      projectIds: [project.id],
    });

    const result = await callTool(app, token.token, "mark_idea_ready", {
      id: `VP-${task.taskNumber}`,
      readiness: { score: 6, reason: "Good enough to start" },
    });

    expect(result.isError).toBeUndefined();
    expect(JSON.parse(result.content[0].text as string).item).toMatchObject({
      readinessScore: 6,
    });
  });

  it("documents every registered tool in the README", () => {
    const readme = readFileSync(join(process.cwd(), "README.md"), "utf8");

    for (const name of mcpToolNames) {
      expect(readme).toContain(`\`${name}\``);
    }
  });

  it("advertises every registered tool", async () => {
    const app = authedApp();
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const token = await store.createApiToken({
      name: "Agent",
      projectIds: [project.id],
    });

    const response = await request(app)
      .post("/mcp")
      .set("Authorization", `Bearer ${token.token}`)
      .set("Accept", "application/json, text/event-stream")
      .set("Content-Type", "application/json")
      .send({ jsonrpc: "2.0", id: 1, method: "tools/list" })
      .expect(200);

    const listed = dataFrame(response.text).result.tools.map(
      (tool: { name: string }) => tool.name,
    );
    expect(listed.sort()).toEqual([...mcpToolNames].sort());

    const info = await request(app).get("/api/mcp-info").expect(200);
    expect(info.body.tools.sort()).toEqual([...mcpToolNames].sort());
  });
});
