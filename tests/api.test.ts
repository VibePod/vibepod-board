import type { Pool } from "pg";
import request from "supertest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createApp } from "../src/server/app.js";
import { createAdminSessionManager } from "../src/server/auth.js";
import { initializeDatabase } from "../src/server/db.js";
import type { PostgresBoardStore } from "../src/server/storage.js";
import { adminAccess } from "../src/server/store.js";
import { resetDatabase } from "./helpers/postgres.js";
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
  const sessions = createAdminSessionManager({
    username: "admin",
    password: "secret",
  });
  const app = createApp({ store, sessions });
  return { app, sessions };
};

const login = async (app: ReturnType<typeof createApp>) => {
  const agent = request.agent(app);
  await agent
    .post("/api/auth/login")
    .send({ username: "admin", password: "secret" })
    .expect(200);
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

    expect((await agent.get("/api/auth/me").expect(200)).body).toEqual({
      authenticated: false,
    });

    await agent
      .post("/api/auth/login")
      .send({ username: "admin", password: "secret" })
      .expect(200);

    expect((await agent.get("/api/auth/me").expect(200)).body).toEqual({
      authenticated: true,
      username: "admin",
    });
  });

  it("exports projects only for admins", async () => {
    const { app } = createAuthedApp();
    const project = await store.createProject({
      key: "APP",
      title: "Application",
    });
    const createdToken = await store.createApiToken({
      name: "Project client",
      projectIds: [project.id],
    });

    await request(app).get(`/api/projects/${project.id}/export`).expect(401);
    await request(app)
      .get(`/api/projects/${project.id}/export`)
      .set("Authorization", `Bearer ${createdToken.token}`)
      .expect(403);

    const agent = await login(app);
    const response = await agent
      .get(`/api/projects/${project.id}/export`)
      .expect(200)
      .expect("Content-Type", /application\/json/)
      .expect("Content-Disposition", 'attachment; filename="APP-project.json"');
    expect(response.body).toMatchObject({
      bundleVersion: 1,
      project: { id: project.id, key: "APP" },
    });
    await agent.get("/api/projects/missing/export").expect(404);
  });

  it("creates and replaces projects through the import endpoint", async () => {
    const { app } = createAuthedApp();
    const source = await store.createProject({
      key: "APP",
      title: "Application",
    });
    await store.createIdea(
      { kind: "admin", username: "admin" },
      { projectId: source.id, title: "Portable task" },
    );
    const bundle = await store.exportProject(source.id);
    const createdToken = await store.createApiToken({
      name: "Project client",
      projectIds: [source.id],
    });

    await request(app)
      .post("/api/projects/import")
      .send({ bundle, replaceExisting: false })
      .expect(401);
    await request(app)
      .post("/api/projects/import")
      .set("Authorization", `Bearer ${createdToken.token}`)
      .send({ bundle, replaceExisting: false })
      .expect(403);

    await resetDatabase(pool);
    await initializeDatabase(pool);
    const agent = await login(app);
    const created = await agent
      .post("/api/projects/import")
      .send({ bundle, replaceExisting: false })
      .expect(201);
    expect(created.body).toMatchObject({
      item: { id: source.id, key: "APP" },
      replaced: false,
    });

    await agent
      .post("/api/projects/import")
      .send({ bundle, replaceExisting: false })
      .expect(409);
    const replaced = await agent
      .post("/api/projects/import")
      .send({ bundle, replaceExisting: true })
      .expect(200);
    expect(replaced.body).toMatchObject({
      item: { id: source.id, key: "APP" },
      replaced: true,
    });
  });

  it("validates and limits project import payloads", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    await agent
      .post("/api/projects/import")
      .send({ bundle: { bundleVersion: 99 }, replaceExisting: false })
      .expect(400);

    const response = await agent
      .post("/api/projects/import")
      .set("Content-Type", "application/json")
      .send(
        JSON.stringify({
          bundle: { padding: "x".repeat(10 * 1024 * 1024) },
        }),
      )
      .expect(413);
    expect(response.body).toEqual({
      error: "Project import file must be 10 MiB or smaller",
    });
  });

  it("allows project tokens to access only mapped project data", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const appProject = await agent
      .post("/api/projects")
      .send({ title: "App", key: "APP" })
      .expect(201);
    const apiProject = await agent
      .post("/api/projects")
      .send({ title: "API", key: "API" })
      .expect(201);
    await agent
      .post("/api/ideas")
      .send({ projectId: appProject.body.item.id, title: "Visible" })
      .expect(201);
    await agent
      .post("/api/ideas")
      .send({ projectId: apiProject.body.item.id, title: "Hidden" })
      .expect(201);

    const tokenResponse = await agent
      .post("/api/tokens")
      .send({ name: "Codex", projectIds: [appProject.body.item.id] })
      .expect(201);
    const token = tokenResponse.body.token;

    const tokenProjects = await request(app)
      .get("/api/projects")
      .set("Authorization", `Bearer ${token}`)
      .expect(200);
    expect(
      tokenProjects.body.items.map((project: { id: string }) => project.id),
    ).toEqual([appProject.body.item.id]);

    const tokenIdeas = await request(app)
      .get("/api/ideas")
      .set("Authorization", `Bearer ${token}`)
      .expect(200);
    expect(
      tokenIdeas.body.items.map((idea: { title: string }) => idea.title),
    ).toEqual(["Visible"]);

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

    await agent
      .post("/api/projects")
      .send({ title: "Missing key" })
      .expect(400);
    await agent
      .post("/api/projects")
      .send({ title: "Invalid key", key: "app" })
      .expect(400);

    const created = await agent
      .post("/api/projects")
      .send({ title: "Launch site", key: "LS" })
      .expect(201);
    expect(created.body.item.key).toBe("LS");

    await agent
      .post("/api/projects")
      .send({ title: "Duplicate", key: "LS" })
      .expect(409);

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
      .send({
        title: "Launch site",
        key: "LS",
        summary: "Coordinate launch work",
      })
      .expect(201);
    const otherProject = await agent
      .post("/api/projects")
      .send({ title: "Backlog", key: "BL" })
      .expect(201);

    const projectId = project.body.item.id;
    const otherProjectId = otherProject.body.item.id;

    const task = await agent
      .post("/api/ideas")
      .send({ projectId, title: "Publish landing page" })
      .expect(201);
    await agent
      .post("/api/ideas")
      .send({ projectId: otherProjectId, title: "Backlog task" })
      .expect(201);
    await agent.post(`/api/ideas/${task.body.item.id}/ready`).expect(200);
    await agent
      .post(`/api/ideas/${task.body.item.id}/sync-github`)
      .send({})
      .expect(200);
    await agent
      .post("/api/documents")
      .send({ projectId, title: "Launch notes", kind: "notes" })
      .expect(201);

    const projects = await agent.get("/api/projects").expect(200);
    expect(
      projects.body.items.map((item: { title: string }) => item.title),
    ).toEqual(["Backlog", "Launch site"]);

    const scopedIdeas = await agent
      .get(`/api/ideas?projectId=${projectId}`)
      .expect(200);
    expect(scopedIdeas.body.items).toHaveLength(1);
    expect(scopedIdeas.body.items[0].projectId).toBe(projectId);

    const scopedBoard = await agent
      .get(`/api/board?projectId=${projectId}`)
      .expect(200);
    expect(scopedBoard.body.columns.ready).toHaveLength(1);
    expect(scopedBoard.body.columns.ready[0].projectId).toBe(projectId);

    const scopedDocuments = await agent
      .get(`/api/documents?projectId=${projectId}`)
      .expect(200);
    expect(scopedDocuments.body.items).toHaveLength(1);
    expect(scopedDocuments.body.items[0].projectId).toBe(projectId);
  });

  it("toggles board availability through the ready endpoint", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const project = await agent
      .post("/api/projects")
      .send({ title: "Launch site", key: "LS" })
      .expect(201);
    const projectId = project.body.item.id;
    const task = await agent
      .post("/api/ideas")
      .send({ projectId, title: "Publish launch checklist" })
      .expect(201);
    const ideaId = task.body.item.id;

    const ready = await agent
      .post(`/api/ideas/${ideaId}/ready`)
      .send({ available: true })
      .expect(200);
    expect(ready.body.item.status).toBe("ready");

    const boardWithTask = await agent
      .get(`/api/board?projectId=${projectId}`)
      .expect(200);
    expect(boardWithTask.body.columns.ready).toHaveLength(1);
    expect(boardWithTask.body.columns.ready[0].ideaId).toBe(ideaId);

    const unavailable = await agent
      .post(`/api/ideas/${ideaId}/ready`)
      .send({ available: false })
      .expect(200);
    expect(unavailable.body.item.status).toBe("idea");

    const boardWithoutTask = await agent
      .get(`/api/board?projectId=${projectId}`)
      .expect(200);
    expect(boardWithoutTask.body.columns.ready).toHaveLength(0);
  });

  it("updates a task to denied status", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const created = await agent
      .post("/api/ideas")
      .send({ title: "Add confetti animation" })
      .expect(201);

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
      .send({
        title: "Kanban processing",
        summary: "Ready issues move through columns",
      })
      .expect(201);

    const response = await agent.get("/api/ideas").expect(200);
    expect(response.body.items).toHaveLength(1);
    expect(response.body.items[0].title).toBe("Kanban processing");
  });

  it("promotes a ready idea into the board via local GitHub sync mode", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const created = await agent
      .post("/api/ideas")
      .send({ title: "Sync bridge" });
    const ideaId = created.body.item.id;

    await agent.post(`/api/ideas/${ideaId}/ready`).expect(200);
    const sync = await agent
      .post(`/api/ideas/${ideaId}/sync-github`)
      .send({})
      .expect(200);

    expect(sync.body.mode).toBe("local");
    expect(sync.body.card.column).toBe("ready");

    const board = await agent.get("/api/board").expect(200);
    expect(board.body.columns.ready).toHaveLength(1);
  });

  it("updates board card branch names through the board API", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const created = await agent
      .post("/api/ideas")
      .send({ title: "Lifecycle state" })
      .expect(201);
    await agent.post(`/api/ideas/${created.body.item.id}/ready`).expect(200);
    const board = await agent.get("/api/board").expect(200);
    const cardId = board.body.columns.ready[0].id;

    const updated = await agent
      .patch(`/api/board/${cardId}`)
      .send({ branchName: "vp-task-lifecycle-state" })
      .expect(200);

    expect(updated.body.item.branchName).toBe("vp-task-lifecycle-state");
    const listed = await agent.get("/api/board").expect(200);
    expect(listed.body.columns.ready[0].branchName).toBe(
      "vp-task-lifecycle-state",
    );
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
        repositoryRemoteUrl: "git@github.com:vibepod/old.git",
      })
      .expect(201);

    await agent.post(`/api/ideas/${created.body.item.id}/ready`).expect(200);

    const updated = await agent
      .patch(`/api/ideas/${created.body.item.id}`)
      .send({
        details: "Implemented in vibepod-cli",
        repositoryLocalPath: "/workspace/vibepod-cli",
        repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git",
      })
      .expect(200);
    expect(updated.body.item).toMatchObject({
      details: "Implemented in vibepod-cli",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git",
    });

    const listed = await agent.get("/api/board").expect(200);
    expect(listed.body.columns.ready[0]).toMatchObject({
      details: "Implemented in vibepod-cli",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git",
    });
  });

  it("updates board card implementation repository metadata through the board API", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const created = await agent
      .post("/api/ideas")
      .send({ title: "Lifecycle state" })
      .expect(201);
    await agent.post(`/api/ideas/${created.body.item.id}/ready`).expect(200);
    const board = await agent.get("/api/board").expect(200);
    const cardId = board.body.columns.ready[0].id;

    const updated = await agent
      .patch(`/api/board/${cardId}`)
      .send({
        details: "Implemented in vibepod-cli",
        repositoryLocalPath: "/workspace/vibepod-cli",
        repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git",
      })
      .expect(200);

    expect(updated.body.item).toMatchObject({
      details: "Implemented in vibepod-cli",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git",
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
        content: "Step-by-step implementation plan",
      })
      .expect(201);

    expect(response.body.item.title).toBe("Execution plan");
    expect(response.body.item.kind).toBe("execution_plan");
  });

  it("sets board card readiness through the API", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const project = await agent
      .post("/api/projects")
      .send({ title: "App", key: "APP" })
      .expect(201);
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
      readinessReason: "Acceptance criteria and repo present",
    });
    expect(response.body.item.readinessEvaluatedAt).toBeDefined();
    expect(response.body.item.updatedAt).toBe(card.updatedAt);

    await agent
      .post(`/api/board/${card.id}/readiness`)
      .send({ score: 42, reason: "r" })
      .expect(400);
    await agent
      .post(`/api/board/${card.id}/readiness`)
      .send({ score: 5 })
      .expect(400);
    await request(app)
      .post(`/api/board/${card.id}/readiness`)
      .send({ score: 5, reason: "r" })
      .expect(401);
  });

  it("sets idea readiness through the API and mirrors it onto the linked card", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const project = await agent
      .post("/api/projects")
      .send({ title: "App", key: "APP" })
      .expect(201);
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
      readinessReason: "Reasonable",
    });
    expect(response.body.item.readinessEvaluatedAt).toBeDefined();

    const board = await agent.get("/api/board").expect(200);
    const card = board.body.columns.ready[0];
    expect(card.readinessScore).toBe(6);
    expect(card.readinessReason).toBe("Reasonable");

    await agent
      .post(`/api/ideas/${idea.body.item.id}/readiness`)
      .send({ score: 99, reason: "x" })
      .expect(400);
    await request(app)
      .post(`/api/ideas/${idea.body.item.id}/readiness`)
      .send({ score: 5, reason: "r" })
      .expect(401);
  });

  it("lists idea readiness history newest-first", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const project = await agent
      .post("/api/projects")
      .send({ title: "App", key: "APP" })
      .expect(201);
    const idea = await agent
      .post("/api/ideas")
      .send({ projectId: project.body.item.id, title: "Tracked" })
      .expect(201);

    await agent
      .post(`/api/ideas/${idea.body.item.id}/readiness`)
      .send({ score: 4, reason: "Rough" })
      .expect(200);
    await agent
      .post(`/api/ideas/${idea.body.item.id}/readiness`)
      .send({ score: 8, reason: "Refined" })
      .expect(200);

    const history = await agent
      .get(`/api/ideas/${idea.body.item.id}/readiness`)
      .expect(200);
    expect(history.body.items).toHaveLength(2);
    expect(history.body.items[0]).toMatchObject({
      score: 8,
      reason: "Refined",
    });
    expect(history.body.items[1]).toMatchObject({ score: 4, reason: "Rough" });
    expect(history.body.items[0].createdAt).toBeDefined();

    await request(app)
      .get(`/api/ideas/${idea.body.item.id}/readiness`)
      .expect(401);
  });

  it("manages task dependencies through the API", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const project = await agent
      .post("/api/projects")
      .send({ title: "App", key: "APP" })
      .expect(201);
    const projectId = project.body.item.id;
    const schema = await agent
      .post("/api/ideas")
      .send({ projectId, title: "Schema" })
      .expect(201);
    const api = await agent
      .post("/api/ideas")
      .send({ projectId, title: "API", dependsOn: [schema.body.item.id] })
      .expect(201);
    expect(api.body.item.dependsOn).toEqual([schema.body.item.id]);
    expect(api.body.item.blockedBy).toEqual([schema.body.item.id]);

    const docs = await agent
      .post("/api/ideas")
      .send({ projectId, title: "Docs" })
      .expect(201);
    const added = await agent
      .post(`/api/ideas/${docs.body.item.id}/dependencies`)
      .send({ dependsOnId: api.body.item.id })
      .expect(201);
    expect(added.body.item.dependsOn).toEqual([api.body.item.id]);

    const replaced = await agent
      .put(`/api/ideas/${docs.body.item.id}/dependencies`)
      .send({ dependsOnIds: [schema.body.item.id, api.body.item.id] })
      .expect(200);
    expect(replaced.body.item.dependsOn.sort()).toEqual(
      [schema.body.item.id, api.body.item.id].sort(),
    );

    const removed = await agent
      .delete(
        `/api/ideas/${docs.body.item.id}/dependencies/${schema.body.item.id}`,
      )
      .expect(200);
    expect(removed.body.item.dependsOn).toEqual([api.body.item.id]);

    await request(app)
      .post(`/api/ideas/${docs.body.item.id}/dependencies`)
      .send({ dependsOnId: schema.body.item.id })
      .expect(401);
  });

  it("rejects self, cross-project, and cyclic dependencies", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const project = await agent
      .post("/api/projects")
      .send({ title: "App", key: "APP" })
      .expect(201);
    const otherProject = await agent
      .post("/api/projects")
      .send({ title: "Ops", key: "OPS" })
      .expect(201);
    const schema = await agent
      .post("/api/ideas")
      .send({ projectId: project.body.item.id, title: "Schema" })
      .expect(201);
    const api = await agent
      .post("/api/ideas")
      .send({ projectId: project.body.item.id, title: "API" })
      .expect(201);
    const outside = await agent
      .post("/api/ideas")
      .send({ projectId: otherProject.body.item.id, title: "Outside" })
      .expect(201);

    await agent
      .post(`/api/ideas/${api.body.item.id}/dependencies`)
      .send({ dependsOnId: api.body.item.id })
      .expect(400);
    await agent
      .post(`/api/ideas/${api.body.item.id}/dependencies`)
      .send({ dependsOnId: outside.body.item.id })
      .expect(400);
    await agent
      .post(`/api/ideas/${api.body.item.id}/dependencies`)
      .send({ dependsOnId: "missing" })
      .expect(404);

    await agent
      .post(`/api/ideas/${api.body.item.id}/dependencies`)
      .send({ dependsOnId: schema.body.item.id })
      .expect(201);
    const cycle = await agent
      .post(`/api/ideas/${schema.body.item.id}/dependencies`)
      .send({ dependsOnId: api.body.item.id })
      .expect(409);
    expect(cycle.body.error).toContain("Dependency cycle detected");
  });

  it("serves a dependency-resolved work order", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const project = await agent
      .post("/api/projects")
      .send({ title: "App", key: "APP" })
      .expect(201);
    const projectId = project.body.item.id;
    const schema = await agent
      .post("/api/ideas")
      .send({ projectId, title: "Schema" })
      .expect(201);
    const api = await agent
      .post("/api/ideas")
      .send({ projectId, title: "API", dependsOn: [schema.body.item.id] })
      .expect(201);

    const workOrder = await agent
      .get("/api/work-order")
      .query({ projectId })
      .expect(200);
    expect(workOrder.body.items.map((item: { id: string }) => item.id)).toEqual(
      [schema.body.item.id, api.body.item.id],
    );
    expect(workOrder.body.items[1]).toMatchObject({
      taskId: "APP-2",
      position: 2,
      wave: 1,
      isBlocked: true,
      isActionable: false,
    });
    expect(workOrder.body.cyclicTaskIds).toEqual([]);

    await agent
      .post(`/api/ideas/${schema.body.item.id}/ready`)
      .send({ available: true })
      .expect(200);
    const board = await agent
      .get("/api/board")
      .query({ projectId })
      .expect(200);
    await agent
      .patch(`/api/board/${board.body.columns.ready[0].id}`)
      .send({ column: "done" })
      .expect(200);

    const unblocked = await agent
      .get("/api/work-order")
      .query({ projectId })
      .expect(200);
    expect(unblocked.body.items[1]).toMatchObject({
      isBlocked: false,
      isActionable: true,
    });

    await request(app).get("/api/work-order").expect(401);
  });

  it("reads one task and one card by key over REST", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Readable",
    });
    await store.markIdeaReady(adminAccess("admin"), task.id);

    const one = await agent.get(`/api/ideas/VP-${task.taskNumber}`).expect(200);
    expect(one.body.item).toMatchObject({ id: task.id, title: "Readable" });

    const card = await agent
      .get(`/api/board/VP-${task.taskNumber}`)
      .expect(200);
    expect(card.body.item).toMatchObject({ ideaId: task.id, column: "ready" });

    // The longer readiness path must still win over the new single-record route.
    const readiness = await agent
      .get(`/api/ideas/${task.id}/readiness`)
      .expect(200);
    expect(readiness.body.items).toEqual([]);

    await agent.get("/api/ideas/VP-999").expect(404);
  });

  it("filters task and board lists over REST", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const ready = await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Ready to go",
    });
    await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Still an idea",
    });
    await store.markIdeaReady(adminAccess("admin"), ready.id);

    const readyOnly = await agent
      .get("/api/ideas?projectId=VP&status=ready")
      .expect(200);
    expect(readyOnly.body.items.map((idea: { id: string }) => idea.id)).toEqual(
      [ready.id],
    );

    const board = await agent.get("/api/board?column=ready").expect(200);
    expect(board.body.columns.ready).toHaveLength(1);
    expect(board.body.columns.planned).toHaveLength(0);

    const future = new Date(Date.now() + 60_000).toISOString();
    const none = await agent
      .get(`/api/ideas?updatedSince=${encodeURIComponent(future)}`)
      .expect(200);
    expect(none.body.items).toEqual([]);
  });

  it("assigns a task and filters by holder over REST", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const holder = "Claude::Subagent101::Worktree12";
    const created = await agent
      .post("/api/ideas")
      .send({ title: "Claimed over REST", assignee: holder })
      .expect(201);
    await agent.post(`/api/ideas/${created.body.item.id}/ready`).expect(200);
    await agent
      .post("/api/ideas")
      .send({ title: "Nobody holds me" })
      .expect(201);

    const mine = await agent
      .get(`/api/ideas?assignee=${encodeURIComponent(holder)}`)
      .expect(200);
    expect(
      mine.body.items.map((idea: { title: string }) => idea.title),
    ).toEqual(["Claimed over REST"]);

    const free = await agent.get("/api/ideas?unassigned=true").expect(200);
    expect(
      free.body.items.map((idea: { title: string }) => idea.title),
    ).toEqual(["Nobody holds me"]);

    const board = await agent
      .get(`/api/board?assignee=${encodeURIComponent(holder)}`)
      .expect(200);
    expect(board.body.columns.ready[0]).toMatchObject({ assignee: holder });

    const released = await agent
      .patch(`/api/ideas/${created.body.item.id}`)
      .send({ assignee: "" })
      .expect(200);
    expect(released.body.item.assignee).toBeUndefined();
  });

  it("claims a task from its board card and writes it through", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const holder = "Codex::Session3";
    const created = await agent
      .post("/api/ideas")
      .send({ title: "Claimed from the board" })
      .expect(201);
    await agent.post(`/api/ideas/${created.body.item.id}/ready`).expect(200);
    const board = await agent.get("/api/board").expect(200);

    await agent
      .patch(`/api/board/${board.body.columns.ready[0].id}`)
      .send({ assignee: holder })
      .expect(200);

    const task = await agent
      .get(`/api/ideas/${created.body.item.id}`)
      .expect(200);
    expect(task.body.item.assignee).toBe(holder);
  });

  it("keeps returning full tasks from REST by default", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Full by default",
      details: "The browser builds its edit form from this",
    });

    const full = await agent.get("/api/ideas").expect(200);
    expect(full.body.items[0].details).toBe(
      "The browser builds its edit form from this",
    );

    const compact = await agent.get("/api/ideas?view=compact").expect(200);
    expect(compact.body.items[0].details).toBeUndefined();
    expect(compact.body.items[0].key).toBe("VP-1");
  });

  it("answers 409 when a guarded write is stale", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const task = await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Contended",
    });
    await store.updateIdea(adminAccess("admin"), task.id, {
      summary: "Moved on",
    });

    const conflict = await agent
      .patch(`/api/ideas/${task.id}`)
      .send({ summary: "Stale", expectedUpdatedAt: task.updatedAt })
      .expect(409);
    expect(conflict.body.error).toContain("changed since");

    const fresh = await agent.get(`/api/ideas/${task.id}`).expect(200);
    await agent
      .patch(`/api/ideas/${task.id}`)
      .send({
        summary: "Fresh",
        expectedUpdatedAt: fresh.body.item.updatedAt,
      })
      .expect(200);
  });

  it("applies a batch of task updates atomically over REST", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    for (const title of ["One", "Two"]) {
      await store.createIdea(adminAccess("admin"), {
        projectId: project.id,
        title,
      });
    }

    const applied = await agent
      .post("/api/ideas/batch")
      .send({
        items: [
          { id: "VP-1", labels: ["api"] },
          { id: "VP-2", labels: ["ui"] },
        ],
      })
      .expect(200);
    expect(applied.body.items).toHaveLength(2);
    expect(applied.body.items[0].key).toBe("VP-1");

    const rejected = await agent
      .post("/api/ideas/batch")
      .send({
        items: [
          { id: "VP-1", labels: ["rolled-back"] },
          { id: "VP-999", labels: ["missing"] },
        ],
      })
      .expect(404);
    expect(rejected.body.error).toContain("Batch item 2 (VP-999)");

    const unchanged = await agent.get("/api/ideas/VP-1").expect(200);
    expect(unchanged.body.item.labels).toEqual(["api"]);
  });

  it("pages the task list when a limit is given", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    for (const title of ["A", "B", "C"]) {
      await store.createIdea(adminAccess("admin"), {
        projectId: project.id,
        title,
      });
    }

    const unlimited = await agent.get("/api/ideas").expect(200);
    expect(unlimited.body.items).toHaveLength(3);
    expect(unlimited.body.nextCursor).toBeUndefined();

    const first = await agent.get("/api/ideas?limit=2").expect(200);
    expect(first.body.items).toHaveLength(2);
    expect(first.body.nextCursor).toBeTruthy();

    const second = await agent
      .get(
        `/api/ideas?limit=2&cursor=${encodeURIComponent(first.body.nextCursor)}`,
      )
      .expect(200);
    expect(second.body.items).toHaveLength(1);
  });

  it("lists readiness for a whole project in one call", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const project = await store.createProject({ key: "VP", title: "VibePod" });
    const rated = await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Rated",
    });
    await store.createIdea(adminAccess("admin"), {
      projectId: project.id,
      title: "Never rated",
    });
    await store.setIdeaReadiness(adminAccess("admin"), rated.id, {
      score: 4,
      reason: "Needs criteria",
    });

    const response = await agent.get("/api/readiness?projectId=VP").expect(200);

    expect(response.body.items).toHaveLength(1);
    expect(response.body.items[0]).toMatchObject({
      ideaId: rated.id,
      score: 4,
    });
  });

  it("accepts a project key and rejects an ambiguous reference", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);
    const vibepod = await store.createProject({ key: "VP", title: "VibePod" });
    await store.createIdea(adminAccess("admin"), {
      projectId: vibepod.id,
      title: "Filed under a key",
    });

    const byKey = await agent.get("/api/ideas?projectId=VP").expect(200);
    expect(byKey.body.items).toHaveLength(1);

    await store.createProject({ key: "BRD", title: "Board" });
    await store.createProject({ key: "BDX", title: "Board" });

    const ambiguous = await agent.get("/api/ideas?projectId=Board").expect(400);
    expect(ambiguous.body.error).toContain("Ambiguous project reference");

    await agent.get("/api/ideas?projectId=Nope").expect(404);
  });
});
