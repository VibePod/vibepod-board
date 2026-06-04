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
  it("validates project IDs on create and update", async () => {
    const app = createApp({ store });

    await request(app).post("/api/projects").send({ title: "Missing key" }).expect(400);
    await request(app).post("/api/projects").send({ title: "Invalid key", key: "app" }).expect(400);

    const created = await request(app)
      .post("/api/projects")
      .send({ title: "Launch site", key: "LS" })
      .expect(201);
    expect(created.body.item.key).toBe("LS");

    await request(app).post("/api/projects").send({ title: "Duplicate", key: "LS" }).expect(409);

    const updated = await request(app)
      .patch(`/api/projects/${created.body.item.id}`)
      .send({ key: "WEB" })
      .expect(200);
    expect(updated.body.item.key).toBe("WEB");
  });

  it("creates projects and scopes project work through query params", async () => {
    const app = createApp({ store });

    const project = await request(app)
      .post("/api/projects")
      .send({ title: "Launch site", key: "LS", summary: "Coordinate launch work" })
      .expect(201);
    const otherProject = await request(app)
      .post("/api/projects")
      .send({ title: "Backlog", key: "BL" })
      .expect(201);

    const projectId = project.body.item.id;
    const otherProjectId = otherProject.body.item.id;

    const task = await request(app)
      .post("/api/ideas")
      .send({ projectId, title: "Publish landing page" })
      .expect(201);
    await request(app).post("/api/ideas").send({ projectId: otherProjectId, title: "Backlog task" }).expect(201);
    await request(app).post(`/api/ideas/${task.body.item.id}/ready`).expect(200);
    await request(app).post(`/api/ideas/${task.body.item.id}/sync-github`).send({}).expect(200);
    await request(app)
      .post("/api/documents")
      .send({ projectId, title: "Launch notes", kind: "notes" })
      .expect(201);

    const projects = await request(app).get("/api/projects").expect(200);
    expect(projects.body.items.map((item: { title: string }) => item.title)).toEqual([
      "Backlog",
      "Launch site"
    ]);

    const scopedIdeas = await request(app).get(`/api/ideas?projectId=${projectId}`).expect(200);
    expect(scopedIdeas.body.items).toHaveLength(1);
    expect(scopedIdeas.body.items[0].projectId).toBe(projectId);

    const scopedBoard = await request(app).get(`/api/board?projectId=${projectId}`).expect(200);
    expect(scopedBoard.body.columns.ready).toHaveLength(1);
    expect(scopedBoard.body.columns.ready[0].projectId).toBe(projectId);

    const scopedDocuments = await request(app).get(`/api/documents?projectId=${projectId}`).expect(200);
    expect(scopedDocuments.body.items).toHaveLength(1);
    expect(scopedDocuments.body.items[0].projectId).toBe(projectId);
  });

  it("toggles board availability through the ready endpoint", async () => {
    const app = createApp({ store });
    const project = await request(app)
      .post("/api/projects")
      .send({ title: "Launch site", key: "LS" })
      .expect(201);
    const projectId = project.body.item.id;
    const task = await request(app)
      .post("/api/ideas")
      .send({ projectId, title: "Publish launch checklist" })
      .expect(201);
    const ideaId = task.body.item.id;

    const ready = await request(app).post(`/api/ideas/${ideaId}/ready`).send({ available: true }).expect(200);
    expect(ready.body.item.status).toBe("ready");

    const boardWithTask = await request(app).get(`/api/board?projectId=${projectId}`).expect(200);
    expect(boardWithTask.body.columns.ready).toHaveLength(1);
    expect(boardWithTask.body.columns.ready[0].ideaId).toBe(ideaId);

    const unavailable = await request(app)
      .post(`/api/ideas/${ideaId}/ready`)
      .send({ available: false })
      .expect(200);
    expect(unavailable.body.item.status).toBe("idea");

    const boardWithoutTask = await request(app).get(`/api/board?projectId=${projectId}`).expect(200);
    expect(boardWithoutTask.body.columns.ready).toHaveLength(0);
  });

  it("updates a task to denied status", async () => {
    const app = createApp({ store });
    const created = await request(app).post("/api/ideas").send({ title: "Add confetti animation" }).expect(201);

    const response = await request(app)
      .patch(`/api/ideas/${created.body.item.id}`)
      .send({ status: "denied" })
      .expect(200);

    expect(response.body.item.status).toBe("denied");

    const aliasResponse = await request(app)
      .patch(`/api/ideas/${created.body.item.id}`)
      .send({ status: "dennied" })
      .expect(200);

    expect(aliasResponse.body.item.status).toBe("denied");
  });

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
