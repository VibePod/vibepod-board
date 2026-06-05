import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

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

describe("API token storage", () => {
  it("creates tokens mapped to projects and stores only a hash", async () => {
    const project = await store.createProject({ key: "APP", title: "App" });

    const created = await store.createApiToken({
      name: "Codex",
      projectIds: [project.id]
    });

    expect(created.token).toMatch(/^vbp_/);
    expect(created.item.name).toBe("Codex");
    expect(created.item.projects).toEqual([{ id: project.id, key: "APP", title: "App" }]);

    const rawRows = await pool.query<{ token_hash: string }>("select token_hash from api_tokens");
    expect(rawRows.rows[0].token_hash).not.toBe(created.token);

    const authenticated = await store.authenticateApiToken(created.token);
    expect(authenticated).toEqual({ tokenId: created.item.id, projectIds: [project.id] });
  });

  it("updates project mappings and rejects revoked tokens", async () => {
    const app = await store.createProject({ key: "APP", title: "App" });
    const api = await store.createProject({ key: "API", title: "API" });
    const created = await store.createApiToken({ name: "Agent", projectIds: [app.id] });

    const updated = await store.updateApiToken(created.item.id, {
      name: "Agent updated",
      projectIds: [api.id]
    });
    expect(updated.name).toBe("Agent updated");
    expect(updated.projects.map((project) => project.id)).toEqual([api.id]);

    await store.revokeApiToken(created.item.id);
    expect(await store.authenticateApiToken(created.token)).toBeNull();
  });
});
