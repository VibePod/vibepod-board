import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { adminAccess } from "../src/server/store.js";
import type { PostgresBoardStore } from "../src/server/storage.js";
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

describe("PostgresBoardStore", () => {
  it("requires unique 1 to 3 capital letter project IDs", async () => {
    await expect(store.createProject({ title: "No key" })).rejects.toThrow("Project ID is required");
    await expect(store.createProject({ title: "Invalid", key: "app" })).rejects.toThrow(
      "Project ID must be 1 to 3 capital letters"
    );

    const project = await store.createProject({ title: "App", key: "APP" });
    expect(project.key).toBe("APP");

    await expect(store.createProject({ title: "Duplicate", key: "APP" })).rejects.toThrow(
      "Project ID APP is already used"
    );

    const otherProject = await store.createProject({ title: "Backlog", key: "BL" });
    expect((await store.updateProject(otherProject.id, { key: "WEB" })).key).toBe("WEB");
    await expect(store.updateProject(otherProject.id, { key: "APP" })).rejects.toThrow(
      "Project ID APP is already used"
    );
  });

  it("assigns incrementing task numbers per project", async () => {
    const app = await store.createProject({ title: "App", key: "APP" });
    const api = await store.createProject({ title: "API", key: "API" });

    const firstAppTask = await store.createIdea(admin, { projectId: app.id, title: "First app task" });
    const secondAppTask = await store.createIdea(admin, { projectId: app.id, title: "Second app task" });
    const firstApiTask = await store.createIdea(admin, { projectId: api.id, title: "First API task" });

    expect(firstAppTask.taskNumber).toBe(1);
    expect(secondAppTask.taskNumber).toBe(2);
    expect(firstApiTask.taskNumber).toBe(1);
  });

  it("stores tasks inside a project", async () => {
    const project = await store.createProject({
      key: "LS",
      title: "Launch site",
      summary: "Coordinate launch work"
    });
    const otherProject = await store.createProject({ title: "Backlog", key: "BL" });

    const idea = await store.createIdea(admin, {
      projectId: project.id,
      title: "Publish landing page",
      summary: "Ship the first project page"
    });
    await store.createIdea(admin, {
      projectId: otherProject.id,
      title: "Unrelated backlog task"
    });

    expect(await store.listProjects()).toHaveLength(2);
    expect((await store.listIdeas(admin, project.id)).map((item) => item.id)).toEqual([idea.id]);
  });

  it("stores denied tasks for rejected ideas", async () => {
    const project = await store.createProject({ title: "Launch site", key: "LS" });
    const idea = await store.createIdea(admin, {
      projectId: project.id,
      title: "Add confetti animation"
    });

    const denied = await store.updateIdea(admin, idea.id, { status: "denied" });

    expect(denied.status).toBe("denied");
    expect((await store.listIdeas(admin, project.id))[0].status).toBe("denied");
  });

  it("refines an idea when details and acceptance criteria are updated", async () => {
    const idea = await store.createIdea(admin, {
      title: "Add GitHub sync",
      summary: "Create GitHub issues from ready ideas",
      details: "",
      labels: ["github", "workflow"]
    });

    const refined = await store.updateIdea(admin, idea.id, {
      details: "Create GitHub issues only when required fields are collected.",
      acceptanceCriteria: ["Ready ideas can be selected", "A board card is created"]
    });

    expect(refined.status).toBe("refining");
    expect(refined.acceptanceCriteria).toEqual(["Ready ideas can be selected", "A board card is created"]);
  });

  it("uses ready as the flag for board availability", async () => {
    const project = await store.createProject({ title: "Launch site", key: "LS" });
    const idea = await store.createIdea(admin, {
      projectId: project.id,
      title: "Publish launch checklist"
    });

    const ready = await store.markIdeaReady(admin, idea.id);
    expect(ready.status).toBe("ready");
    expect((await store.getBoardColumns(admin, project.id)).ready).toHaveLength(1);
    expect((await store.getBoardColumns(admin, project.id)).ready[0].ideaId).toBe(idea.id);

    const unavailable = await store.setIdeaBoardAvailability(admin, idea.id, false);
    expect(unavailable.status).toBe("idea");
    expect((await store.getBoardColumns(admin, project.id)).ready).toHaveLength(0);
  });

  it("moves board cards between any columns without workflow constraints", async () => {
    const project = await store.createProject({ title: "Launch site", key: "LS" });
    const idea = await store.createIdea(admin, {
      projectId: project.id,
      title: "Publish launch checklist"
    });
    await store.markIdeaReady(admin, idea.id);
    const [card] = (await store.getBoardColumns(admin, project.id)).ready;

    expect((await store.moveBoardCard(admin, card.id, "done")).column).toBe("done");
    expect((await store.moveBoardCard(admin, card.id, "planned")).column).toBe("planned");
  });

  it("stores the implementation branch name on board cards", async () => {
    const project = await store.createProject({ title: "CLI", key: "CLI" });
    const idea = await store.createIdea(admin, {
      projectId: project.id,
      title: "Persist lifecycle state"
    });
    await store.markIdeaReady(admin, idea.id);
    const [card] = (await store.getBoardColumns(admin, project.id)).ready;

    const updated = await store.updateBoardCard(admin, card.id, {
      branchName: "vp-task-lifecycle-state"
    });

    expect(updated.branchName).toBe("vp-task-lifecycle-state");
    expect((await store.getBoardColumns(admin, project.id)).ready[0].branchName).toBe(
      "vp-task-lifecycle-state"
    );
  });

  it("copies repository metadata from ready tasks onto board cards", async () => {
    const project = await store.createProject({ title: "CLI", key: "CLI" });
    const idea = await store.createIdea(admin, {
      projectId: project.id,
      title: "Persist lifecycle state",
      details: "Write lifecycle files in the CLI repository.",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
    });

    await store.markIdeaReady(admin, idea.id);
    const [card] = (await store.getBoardColumns(admin, project.id)).ready;

    expect(card).toMatchObject({
      ideaId: idea.id,
      details: "Write lifecycle files in the CLI repository.",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
    });
  });

  it("syncs edited task details and repository metadata to an existing board card", async () => {
    const project = await store.createProject({ title: "CLI", key: "CLI" });
    const idea = await store.createIdea(admin, {
      projectId: project.id,
      title: "Persist lifecycle state",
      details: "Initial implementation details",
      repositoryLocalPath: "/workspace/old",
      repositoryRemoteUrl: "git@github.com:vibepod/old.git"
    });
    await store.markIdeaReady(admin, idea.id);
    const [card] = (await store.getBoardColumns(admin, project.id)).ready;
    await store.updateBoardCard(admin, card.id, { branchName: "vp-task-lifecycle-state" });

    const updated = await store.updateIdea(admin, idea.id, {
      details: "Updated implementation details",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
    });
    const [updatedCard] = (await store.getBoardColumns(admin, project.id)).ready;

    expect(updated).toMatchObject({
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
    });
    expect(updatedCard).toMatchObject({
      branchName: "vp-task-lifecycle-state",
      details: "Updated implementation details",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
    });
  });

  it("updates board card implementation details and repository metadata directly", async () => {
    const project = await store.createProject({ title: "CLI", key: "CLI" });
    const idea = await store.createIdea(admin, {
      projectId: project.id,
      title: "Persist lifecycle state"
    });
    await store.markIdeaReady(admin, idea.id);
    const [card] = (await store.getBoardColumns(admin, project.id)).ready;

    const updated = await store.updateBoardCard(admin, card.id, {
      details: "Implemented in vibepod-cli",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
    });

    expect(updated).toMatchObject({
      details: "Implemented in vibepod-cli",
      repositoryLocalPath: "/workspace/vibepod-cli",
      repositoryRemoteUrl: "git@github.com:vibepod/vibepod-cli.git"
    });
  });

  it("stores execution plan documents linked to ideas and board cards", async () => {
    const idea = await store.createIdea(admin, { title: "MCP bridge", labels: ["mcp"] });
    const card = await store.createBoardCardFromIdea(admin, (await store.markIdeaReady(admin, idea.id)).id, {
      githubMode: "local"
    });

    const document = await store.createDocument(admin, {
      title: "MCP Bridge Execution Plan",
      kind: "execution_plan",
      content: "Build MCP tools for reading and updating the board.",
      linkedIdeaIds: [idea.id],
      linkedCardIds: [card.id]
    });

    expect(document.kind).toBe("execution_plan");
    expect(document.linkedIdeaIds).toEqual([idea.id]);
    expect(document.linkedCardIds).toEqual([card.id]);
  });

  it("exports scoped state for admins and project tokens", async () => {
    const project = await store.createProject({ title: "Launch site", key: "LS" });
    const otherProject = await store.createProject({ title: "Backlog", key: "BL" });
    await store.createIdea(admin, { projectId: project.id, title: "Scoped task" });
    await store.createIdea(admin, { projectId: otherProject.id, title: "Hidden task" });

    const fullState = await store.getState(admin);
    const scopedState = await store.getState({ kind: "token", tokenId: "token-1", projectIds: [project.id] });

    expect(fullState.projects.map((item) => item.id).sort()).toEqual([otherProject.id, project.id].sort());
    expect(scopedState.projects.map((item) => item.id)).toEqual([project.id]);
    expect(scopedState.ideas.map((item) => item.title)).toEqual(["Scoped task"]);
  });
});
