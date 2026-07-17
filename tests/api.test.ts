import type { Pool } from "pg";
import request from "supertest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createAdminSessionManager } from "../src/server/auth.js";
import { createApp } from "../src/server/app.js";
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

const createAuthedApp = () => {
  const sessions = createAdminSessionManager({ username: "admin", password: "secret" });
  const app = createApp({ store, sessions });
  return { app, sessions };
};

const login = async (app: ReturnType<typeof createApp>) => {
  const agent = request.agent(app);
  await agent.post("/api/auth/login").send({ username: "admin", password: "secret" }).expect(200);
  return agent;
};

describe("API", () => {
  it("requires admin auth for REST board endpoints", async () => {
    const { app } = createAuthedApp();

    await request(app).get("/api/health").expect(200);
    await request(app).get("/api/projects").expect(401);

    const agent = await login(app);
    await agent.get("/api/projects").expect(200);
    await agent.post("/api/auth/logout").expect(200);
    await agent.get("/api/projects").expect(401);
  });

  it("reports auth state through /api/auth/me", async () => {
    const { app } = createAuthedApp();
    const agent = request.agent(app);

    expect((await agent.get("/api/auth/me").expect(200)).body).toEqual({ authenticated: false });

    await agent.post("/api/auth/login").send({ username: "admin", password: "secret" }).expect(200);

    expect((await agent.get("/api/auth/me").expect(200)).body).toEqual({
      authenticated: true,
      username: "admin"
    });
  });

  it("allows project tokens to access only mapped project data", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const appProject = await agent.post("/api/projects").send({ title: "App", key: "APP" }).expect(201);
    const apiProject = await agent.post("/api/projects").send({ title: "API", key: "API" }).expect(201);
    await agent.post("/api/ideas").send({ projectId: appProject.body.item.id, title: "Visible" }).expect(201);
    await agent.post("/api/ideas").send({ projectId: apiProject.body.item.id, title: "Hidden" }).expect(201);

    const tokenResponse = await agent
      .post("/api/tokens")
      .send({ name: "Codex", projectIds: [appProject.body.item.id] })
      .expect(201);
    const token = tokenResponse.body.token;

    const tokenProjects = await request(app)
      .get("/api/projects")
      .set("Authorization", `Bearer ${token}`)
      .expect(200);
    expect(tokenProjects.body.items.map((project: { id: string }) => project.id)).toEqual([
      appProject.body.item.id
    ]);

    const tokenIdeas = await request(app)
      .get("/api/ideas")
      .set("Authorization", `Bearer ${token}`)
      .expect(200);
    expect(tokenIdeas.body.items.map((idea: { title: string }) => idea.title)).toEqual(["Visible"]);

    await request(app)
      .post("/api/ideas")
      .set("Authorization", `Bearer ${token}`)
      .send({ projectId: apiProject.body.item.id, title: "Forbidden" })
      .expect(403);

    await request(app)
      .post("/api/projects")
      .set("Authorization", `Bearer ${token}`)
      .send({ key: "NEW", title: "Nope" })
      .expect(403);
  });

  it("validates project IDs on create and update", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    await agent.post("/api/projects").send({ title: "Missing key" }).expect(400);
    await agent.post("/api/projects").send({ title: "Invalid key", key: "app" }).expect(400);

    const created = await agent
      .post("/api/projects")
      .send({ title: "Launch site", key: "LS" })
      .expect(201);
    expect(created.body.item.key).toBe("LS");

    await agent.post("/api/projects").send({ title: "Duplicate", key: "LS" }).expect(409);

    const updated = await agent
      .patch(`/api/projects/${created.body.item.id}`)
      .send({ key: "WEB" })
      .expect(200);
    expect(updated.body.item.key).toBe("WEB");
  });

  it("creates projects and scopes project work through query params", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const project = await agent
      .post("/api/projects")
      .send({ title: "Launch site", key: "LS", summary: "Coordinate launch work" })
      .expect(201);
    const otherProject = await agent
      .post("/api/projects")
      .send({ title: "Backlog", key: "BL" })
      .expect(201);

    const projectId = project.body.item.id;
    const otherProjectId = otherProject.body.item.id;

    const task = await agent.post("/api/ideas").send({ projectId, title: "Publish landing page" }).expect(201);
    await agent.post("/api/ideas").send({ projectId: otherProjectId, title: "Backlog task" }).expect(201);
    await agent.post(`/api/ideas/${task.body.item.id}/ready`).expect(200);
    await agent.post(`/api/ideas/${task.body.item.id}/sync-github`).send({}).expect(200);
    await agent.post("/api/documents").send({ projectId, title: "Launch notes", kind: "notes" }).expect(201);

    const projects = await agent.get("/api/projects").expect(200);
    expect(projects.body.items.map((item: { title: string }) => item.title)).toEqual([
      "Backlog",
      "Launch site"
    ]);

    const scopedIdeas = await agent.get(`/api/ideas?projectId=${projectId}`).expect(200);
    expect(scopedIdeas.body.items).toHaveLength(1);
    expect(scopedIdeas.body.items[0].projectId).toBe(projectId);

    const scopedBoard = await agent.get(`/api/board?projectId=${projectId}`).expect(200);
    expect(scopedBoard.body.columns.ready).toHaveLength(1);
    expect(scopedBoard.body.columns.ready[0].projectId).toBe(projectId);

    const scopedDocuments = await agent.get(`/api/documents?projectId=${projectId}`).expect(200);
    expect(scopedDocuments.body.items).toHaveLength(1);
    expect(scopedDocuments.body.items[0].projectId).toBe(projectId);
  });

  it("toggles board availability through the ready endpoint", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const project = await agent.post("/api/projects").send({ title: "Launch site", key: "LS" }).expect(201);
    const projectId = project.body.item.id;
    const task = await agent.post("/api/ideas").send({ projectId, title: "Publish launch checklist" }).expect(201);
    const ideaId = task.body.item.id;

    const ready = await agent.post(`/api/ideas/${ideaId}/ready`).send({ available: true }).expect(200);
    expect(ready.body.item.status).toBe("ready");

    const boardWithTask = await agent.get(`/api/board?projectId=${projectId}`).expect(200);
    expect(boardWithTask.body.columns.ready).toHaveLength(1);
    expect(boardWithTask.body.columns.ready[0].ideaId).toBe(ideaId);

    const unavailable = await agent.post(`/api/ideas/${ideaId}/ready`).send({ available: false }).expect(200);
    expect(unavailable.body.item.status).toBe("idea");

    const boardWithoutTask = await agent.get(`/api/board?projectId=${projectId}`).expect(200);
    expect(boardWithoutTask.body.columns.ready).toHaveLength(0);
  });

  it("updates a task to denied status", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const created = await agent.post("/api/ideas").send({ title: "Add confetti animation" }).expect(201);

    const response = await agent
      .patch(`/api/ideas/${created.body.item.id}`)
      .send({ status: "denied" })
      .expect(200);

    expect(response.body.item.status).toBe("denied");

    const aliasResponse = await agent
      .patch(`/api/ideas/${created.body.item.id}`)
      .send({ status: "dennied" })
      .expect(200);

    expect(aliasResponse.body.item.status).toBe("denied");
  });

  it("creates ideas and lists them", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    await agent
      .post("/api/ideas")
      .send({ title: "Kanban processing", summary: "Ready issues move through columns" })
      .expect(201);

    const response = await agent.get("/api/ideas").expect(200);
    expect(response.body.items).toHaveLength(1);
    expect(response.body.items[0].title).toBe("Kanban processing");
  });

  it("promotes a ready idea into the board via local GitHub sync mode", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const created = await agent.post("/api/ideas").send({ title: "Sync bridge" });
    const ideaId = created.body.item.id;

    await agent.post(`/api/ideas/${ideaId}/ready`).expect(200);
    const sync = await agent.post(`/api/ideas/${ideaId}/sync-github`).send({}).expect(200);

    expect(sync.body.mode).toBe("local");
    expect(sync.body.card.column).toBe("ready");

    const board = await agent.get("/api/board").expect(200);
    expect(board.body.columns.ready).toHaveLength(1);
  });

  it("updates board card branch names through the board API", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const created = await agent.post("/api/ideas").send({ title: "Lifecycle state" }).expect(201);
    await agent.post(`/api/ideas/${created.body.item.id}/ready`).expect(200);
    const board = await agent.get("/api/board").expect(200);
    const cardId = board.body.columns.ready[0].id;

    const updated = await agent
      .patch(`/api/board/${cardId}`)
      .send({ branchName: "vp-task-lifecycle-state" })
      .expect(200);

    expect(updated.body.item.branchName).toBe("vp-task-lifecycle-state");
    const listed = await agent.get("/api/board").expect(200);
    expect(listed.body.columns.ready[0].branchName).toBe("vp-task-lifecycle-state");
  });

  it("updates task repository metadata and syncs it to the board through the API", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const created = await agent
      .post("/api/ideas")
      .send({
        title: "Lifecycle state",
        details: "Initial task details",
        repositoryLocalPath: "/workspace/old",
        repositoryRemoteUrl: "git@github.com:vibepod/old.git"
      })
      .expect(201);

    await agent.post(`/api/ideas/${created.body.item.id}/ready`).expect(200);

    const updated = await agent
      .patch(`/api/ideas/${created.body.item.id}`)
      .send({
        details: "Implemented in vibepod-cli",
        repositoryLocalPath: "/workspace/vibepod-cli",
        repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
      })
      .expect(200);
    expect(updated.body.item).toMatchObject({
      details: "Implemented in vibepod-cli",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
    });

    const listed = await agent.get("/api/board").expect(200);
    expect(listed.body.columns.ready[0]).toMatchObject({
      details: "Implemented in vibepod-cli",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
    });
  });

  it("updates board card implementation repository metadata through the board API", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const created = await agent.post("/api/ideas").send({ title: "Lifecycle state" }).expect(201);
    await agent.post(`/api/ideas/${created.body.item.id}/ready`).expect(200);
    const board = await agent.get("/api/board").expect(200);
    const cardId = board.body.columns.ready[0].id;

    const updated = await agent
      .patch(`/api/board/${cardId}`)
      .send({
        details: "Implemented in vibepod-cli",
        repositoryLocalPath: "/workspace/vibepod-cli",
        repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
      })
      .expect(200);

    expect(updated.body.item).toMatchObject({
      details: "Implemented in vibepod-cli",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
    });
  });

  it("creates documents through the API", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const response = await agent
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

  it("sets board card readiness through the API", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const project = await agent.post("/api/projects").send({ title: "App", key: "APP" }).expect(201);
    const idea = await agent
      .post("/api/ideas")
      .send({ projectId: project.body.item.id, title: "Scored" })
      .expect(201);
    await agent.post(`/api/ideas/${idea.body.item.id}/ready`).expect(200);
    const board = await agent.get("/api/board").expect(200);
    const card = board.body.columns.ready[0];

    const response = await agent
      .post(`/api/board/${card.id}/readiness`)
      .send({ score: 8, reason: "Acceptance criteria and repo present" })
      .expect(200);

    expect(response.body.item).toMatchObject({
      id: card.id,
      readinessScore: 8,
      readinessReason: "Acceptance criteria and repo present"
    });
    expect(response.body.item.readinessEvaluatedAt).toBeDefined();
    expect(response.body.item.updatedAt).toBe(card.updatedAt);

    await agent.post(`/api/board/${card.id}/readiness`).send({ score: 42, reason: "r" }).expect(400);
    await agent.post(`/api/board/${card.id}/readiness`).send({ score: 5 }).expect(400);
    await request(app)
      .post(`/api/board/${card.id}/readiness`)
      .send({ score: 5, reason: "r" })
      .expect(401);
  });

  it("sets idea readiness through the API and mirrors it onto the linked card", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const project = await agent.post("/api/projects").send({ title: "App", key: "APP" }).expect(201);
    const idea = await agent
      .post("/api/ideas")
      .send({ projectId: project.body.item.id, title: "Scored idea" })
      .expect(201);
    await agent.post(`/api/ideas/${idea.body.item.id}/ready`).expect(200);

    const response = await agent
      .post(`/api/ideas/${idea.body.item.id}/readiness`)
      .send({ score: 6, reason: "Reasonable" })
      .expect(200);

    expect(response.body.item).toMatchObject({
      id: idea.body.item.id,
      readinessScore: 6,
      readinessReason: "Reasonable"
    });
    expect(response.body.item.readinessEvaluatedAt).toBeDefined();

    const board = await agent.get("/api/board").expect(200);
    const card = board.body.columns.ready[0];
    expect(card.readinessScore).toBe(6);
    expect(card.readinessReason).toBe("Reasonable");

    await agent.post(`/api/ideas/${idea.body.item.id}/readiness`).send({ score: 99, reason: "x" }).expect(400);
    await request(app)
      .post(`/api/ideas/${idea.body.item.id}/readiness`)
      .send({ score: 5, reason: "r" })
      .expect(401);
  });
});
