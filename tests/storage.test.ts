import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { BoardStore } from "../src/server/storage.js";

let tempDir: string;
let store: BoardStore;

beforeEach(async () => {
  tempDir = await mkdtemp(join(tmpdir(), "vibepod-board-"));
  store = new BoardStore(join(tempDir, "board.json"));
});

afterEach(async () => {
  await rm(tempDir, { recursive: true, force: true });
});

describe("BoardStore", () => {
  it("requires unique 1 to 3 capital letter project IDs", () => {
    expect(() => store.createProject({ title: "No key" })).toThrow("Project ID is required");
    expect(() => store.createProject({ title: "Invalid", key: "app" })).toThrow(
      "Project ID must be 1 to 3 capital letters"
    );

    const project = store.createProject({ title: "App", key: "APP" });
    expect(project.key).toBe("APP");

    expect(() => store.createProject({ title: "Duplicate", key: "APP" })).toThrow(
      "Project ID APP is already used"
    );

    const otherProject = store.createProject({ title: "Backlog", key: "BL" });
    expect(store.updateProject(otherProject.id, { key: "WEB" }).key).toBe("WEB");
    expect(() => store.updateProject(otherProject.id, { key: "APP" })).toThrow(
      "Project ID APP is already used"
    );
  });

  it("assigns incrementing task numbers per project", () => {
    const app = store.createProject({ title: "App", key: "APP" });
    const api = store.createProject({ title: "API", key: "API" });

    const firstAppTask = store.createIdea({ projectId: app.id, title: "First app task" });
    const secondAppTask = store.createIdea({ projectId: app.id, title: "Second app task" });
    const firstApiTask = store.createIdea({ projectId: api.id, title: "First API task" });

    expect(firstAppTask.taskNumber).toBe(1);
    expect(secondAppTask.taskNumber).toBe(2);
    expect(firstApiTask.taskNumber).toBe(1);
  });

  it("migrates existing work into a default project", async () => {
    const filePath = join(tempDir, "legacy-board.json");
    const timestamp = "2026-01-01T00:00:00.000Z";
    await writeFile(
      filePath,
      `${JSON.stringify(
        {
          schemaVersion: 1,
          ideas: [
            {
              id: "idea-1",
              title: "Legacy task",
              summary: "",
              details: "",
              status: "idea",
              labels: [],
              acceptanceCriteria: [],
              createdAt: timestamp,
              updatedAt: timestamp
            }
          ],
          boardCards: [
            {
              id: "card-1",
              title: "Legacy card",
              details: "",
              column: "ready",
              ideaId: "idea-1",
              labels: [],
              createdAt: timestamp,
              updatedAt: timestamp
            }
          ],
          documents: [
            {
              id: "document-1",
              title: "Legacy note",
              kind: "notes",
              content: "",
              linkedIdeaIds: ["idea-1"],
              linkedCardIds: ["card-1"],
              createdAt: timestamp,
              updatedAt: timestamp
            }
          ],
          activity: []
        },
        null,
        2
      )}\n`,
      "utf8"
    );

    const migratedStore = new BoardStore(filePath);
    const [project] = migratedStore.listProjects();
    const saved = JSON.parse(await readFile(filePath, "utf8")) as { schemaVersion: number };

    expect(project.title).toBe("General");
    expect(project.key).toBe("GEN");
    expect(migratedStore.listIdeas(project.id)[0].projectId).toBe(project.id);
    expect(migratedStore.listIdeas(project.id)[0].taskNumber).toBe(1);
    expect(migratedStore.getBoardColumns(project.id).ready[0].projectId).toBe(project.id);
    expect(migratedStore.listDocuments(project.id)[0].projectId).toBe(project.id);
    expect(saved.schemaVersion).toBe(3);
  });

  it("stores tasks, board cards, and documents inside a project", () => {
    const project = store.createProject({
      key: "LS",
      title: "Launch site",
      summary: "Coordinate launch work"
    });
    const otherProject = store.createProject({ title: "Backlog", key: "BL" });

    const idea = store.createIdea({
      projectId: project.id,
      title: "Publish landing page",
      summary: "Ship the first project page"
    });
    store.createIdea({
      projectId: otherProject.id,
      title: "Unrelated backlog task"
    });
    const card = store.createBoardCardFromIdea(store.markIdeaReady(idea.id).id, {
      githubMode: "local"
    });
    const document = store.createDocument({
      projectId: project.id,
      title: "Launch notes",
      kind: "notes",
      content: "Release checklist"
    });

    expect(store.listProjects()).toHaveLength(2);
    expect(store.listIdeas(project.id).map((item) => item.id)).toEqual([idea.id]);
    expect(store.getBoardColumns(project.id).ready.map((item) => item.id)).toEqual([card.id]);
    expect(store.listDocuments(project.id).map((item) => item.id)).toEqual([document.id]);
  });

  it("uses ready as the flag for board availability", () => {
    const project = store.createProject({ title: "Launch site", key: "LS" });
    const idea = store.createIdea({
      projectId: project.id,
      title: "Publish launch checklist"
    });

    const ready = store.markIdeaReady(idea.id);
    expect(ready.status).toBe("ready");
    expect(store.getBoardColumns(project.id).ready).toHaveLength(1);
    expect(store.getBoardColumns(project.id).ready[0].ideaId).toBe(idea.id);

    const unavailable = store.setIdeaBoardAvailability(idea.id, false);
    expect(unavailable.status).toBe("idea");
    expect(store.getBoardColumns(project.id).ready).toHaveLength(0);
  });

  it("moves board cards between any columns without workflow constraints", () => {
    const project = store.createProject({ title: "Launch site", key: "LS" });
    const idea = store.createIdea({
      projectId: project.id,
      title: "Publish launch checklist"
    });
    store.markIdeaReady(idea.id);
    const [card] = store.getBoardColumns(project.id).ready;

    expect(store.moveBoardCard(card.id, "done").column).toBe("done");
    expect(store.moveBoardCard(card.id, "planned").column).toBe("planned");
  });

  it("stores denied tasks for rejected ideas", () => {
    const project = store.createProject({ title: "Launch site", key: "LS" });
    const idea = store.createIdea({
      projectId: project.id,
      title: "Add confetti animation"
    });

    const denied = store.updateIdea(idea.id, { status: "denied" });

    expect(denied.status).toBe("denied");
    expect(store.listIdeas(project.id)[0].status).toBe("denied");
  });

  it("migrates legacy synced tasks to ready status", async () => {
    const filePath = join(tempDir, "synced-board.json");
    const timestamp = "2026-01-01T00:00:00.000Z";
    await writeFile(
      filePath,
      `${JSON.stringify(
        {
          schemaVersion: 2,
          projects: [
            {
              id: "project-1",
              title: "Launch site",
              summary: "",
              createdAt: timestamp,
              updatedAt: timestamp
            }
          ],
          ideas: [
            {
              id: "idea-1",
              projectId: "project-1",
              title: "Legacy synced task",
              summary: "",
              details: "",
              status: "synced",
              labels: [],
              acceptanceCriteria: [],
              createdAt: timestamp,
              updatedAt: timestamp
            }
          ],
          boardCards: [],
          documents: [],
          activity: []
        },
        null,
        2
      )}\n`,
      "utf8"
    );

    const migratedStore = new BoardStore(filePath);

    expect(migratedStore.listIdeas("project-1")[0].status).toBe("ready");
  });

  it("refines an idea, marks it ready, and promotes it to a board card", () => {
    const idea = store.createIdea({
      title: "Add GitHub sync",
      summary: "Create GitHub issues from ready ideas",
      details: "The user should be able to refine the idea before syncing.",
      labels: ["github", "workflow"]
    });

    const refined = store.updateIdea(idea.id, {
      details: "Create GitHub issues only when required fields are collected.",
      acceptanceCriteria: ["Ready ideas can be selected", "A board card is created"]
    });
    const ready = store.markIdeaReady(refined.id);
    const card = store.createBoardCardFromIdea(ready.id, { githubMode: "local" });

    expect(ready.status).toBe("ready");
    expect(card.ideaId).toBe(idea.id);
    expect(card.column).toBe("ready");
    expect(card.labels).toContain("github");
  });

  it("stores execution plan documents linked to ideas and board cards", () => {
    const idea = store.createIdea({ title: "MCP bridge", labels: ["mcp"] });
    const card = store.createBoardCardFromIdea(store.markIdeaReady(idea.id).id, {
      githubMode: "local"
    });

    const document = store.createDocument({
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
});
