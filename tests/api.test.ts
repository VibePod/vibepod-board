import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import request from "supertest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createApp } from "../src/server/app.js";
import { BoardStore } from "../src/server/storage.js";

let tempDir: string;
let store: BoardStore;

beforeEach(async () => {
  tempDir = await mkdtemp(join(tmpdir(), "vibepod-board-api-"));
  store = new BoardStore(join(tempDir, "board.json"));
});

afterEach(async () => {
  await rm(tempDir, { recursive: true, force: true });
});

describe("API", () => {
  it("creates ideas and lists them", async () => {
    const app = createApp({ store });

    await request(app)
      .post("/api/ideas")
      .send({ title: "Kanban processing", summary: "Ready issues move through columns" })
      .expect(201);

    const response = await request(app).get("/api/ideas").expect(200);
    expect(response.body.items).toHaveLength(1);
    expect(response.body.items[0].title).toBe("Kanban processing");
  });

  it("promotes a ready idea into the board via local GitHub sync mode", async () => {
    const app = createApp({ store });
    const created = await request(app).post("/api/ideas").send({ title: "Sync bridge" });
    const ideaId = created.body.item.id;

    await request(app).post(`/api/ideas/${ideaId}/ready`).expect(200);
    const sync = await request(app).post(`/api/ideas/${ideaId}/sync-github`).send({}).expect(200);

    expect(sync.body.mode).toBe("local");
    expect(sync.body.card.column).toBe("ready");

    const board = await request(app).get("/api/board").expect(200);
    expect(board.body.columns.ready).toHaveLength(1);
  });

  it("creates documents through the API", async () => {
    const app = createApp({ store });

    const response = await request(app)
      .post("/api/documents")
      .send({
        title: "Execution plan",
        kind: "execution_plan",
        content: "Step-by-step implementation plan"
      })
      .expect(201);

    expect(response.body.item.title).toBe("Execution plan");
    expect(response.body.item.kind).toBe("execution_plan");
  });
});
