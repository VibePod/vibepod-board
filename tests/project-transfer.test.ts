import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { initializeDatabase } from "../src/server/db.js";
import type { PostgresBoardStore } from "../src/server/storage.js";
import { adminAccess } from "../src/server/store.js";
import type { ProjectBundle } from "../src/shared/types.js";
import { resetDatabase } from "./helpers/postgres.js";
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

const createTransferBundle = async (): Promise<ProjectBundle> => {
  const project = await store.createProject({
    key: "APP",
    title: "Imported Application",
    summary: "Source data",
  });
  const foundation = await store.createIdea(admin, {
    projectId: project.id,
    title: "Imported foundation",
  });
  const feature = await store.createIdea(admin, {
    projectId: project.id,
    title: "Imported feature",
    dependsOn: [foundation.id],
  });
  await store.markIdeaReady(admin, foundation.id);
  const card = (await store.listBoardCards(admin, project.id))[0];
  await store.setIdeaReadiness(admin, foundation.id, {
    score: 8,
    reason: "Ready to move",
  });
  await store.createDocument(admin, {
    projectId: project.id,
    title: "Imported plan",
    linkedIdeaIds: [feature.id],
    linkedCardIds: [card.id],
  });
  return store.exportProject(project.id);
};

describe("project transfer", () => {
  it("exports one self-contained project", async () => {
    const project = await store.createProject({
      key: "APP",
      title: "Application",
      summary: "Portable app project",
    });
    const otherProject = await store.createProject({
      key: "OTH",
      title: "Other",
    });
    const foundation = await store.createIdea(admin, {
      projectId: project.id,
      title: "Foundation",
    });
    const feature = await store.createIdea(admin, {
      projectId: project.id,
      title: "Feature",
      dependsOn: [foundation.id],
    });
    await store.createIdea(admin, {
      projectId: otherProject.id,
      title: "Other project task",
    });
    await store.markIdeaReady(admin, foundation.id);
    const card = (await store.listBoardCards(admin, project.id))[0];
    await store.moveBoardCard(admin, card.id, "planned");
    await store.setIdeaReadiness(admin, foundation.id, {
      score: 8,
      reason: "Clear",
    });
    await store.createDocument(admin, {
      projectId: project.id,
      title: "Plan",
      content: "Ship it",
      linkedIdeaIds: [foundation.id],
      linkedCardIds: [card.id],
    });

    const bundle = await store.exportProject(project.id);

    expect(bundle).toMatchObject({
      bundleVersion: 1,
      project: { id: project.id, key: "APP" },
    });
    expect(bundle.ideas).toHaveLength(2);
    expect(bundle.ideas.map((idea) => idea.projectId)).toEqual([
      project.id,
      project.id,
    ]);
    expect(
      bundle.ideas.find((idea) => idea.id === feature.id)?.dependsOn,
    ).toEqual([foundation.id]);
    expect(bundle.boardCards).toHaveLength(1);
    expect(bundle.boardCards[0].column).toBe("planned");
    expect(bundle.readinessEvents).toHaveLength(1);
    expect(bundle.documents[0]).toMatchObject({
      linkedIdeaIds: [foundation.id],
      linkedCardIds: [card.id],
    });
    expect(JSON.stringify(bundle)).not.toContain("Other project task");
    await expect(store.exportProject("missing")).rejects.toThrow(
      "Project not found: missing",
    );
  });

  it("imports a bundle as a new project", async () => {
    const project = await store.createProject({
      key: "APP",
      title: "Application",
      summary: "Portable app project",
    });
    const foundation = await store.createIdea(admin, {
      projectId: project.id,
      title: "Foundation",
      repositoryLocalPath: "/workspace/app",
      repositoryRemoteUrl: "git@github.com:example/app.git",
      assignee: "Claude::Subagent101::Worktree12",
    });
    const feature = await store.createIdea(admin, {
      projectId: project.id,
      title: "Feature",
      dependsOn: [foundation.id],
    });
    await store.markIdeaReady(admin, foundation.id);
    const card = (await store.listBoardCards(admin, project.id))[0];
    await store.moveBoardCard(admin, card.id, "planned");
    await store.setIdeaReadiness(admin, foundation.id, {
      score: 8,
      reason: "Clear",
    });
    await store.createDocument(admin, {
      projectId: project.id,
      title: "Plan",
      content: "Ship it",
      linkedIdeaIds: [feature.id],
      linkedCardIds: [card.id],
    });
    const bundle = await store.exportProject(project.id);

    await resetDatabase(pool);
    await initializeDatabase(pool);

    const result = await store.importProject(bundle, {
      replaceExisting: false,
    });
    const roundTrip = await store.exportProject(bundle.project.id);

    expect(result).toEqual({ item: bundle.project, replaced: false });
    expect(roundTrip).toMatchObject({
      project: bundle.project,
      ideas: bundle.ideas,
      boardCards: bundle.boardCards,
      readinessEvents: bundle.readinessEvents,
      documents: bundle.documents,
    });
    expect((await store.listActivity(admin))[0]).toMatchObject({
      type: "project.imported",
      message: "Imported project: Application",
    });
    expect(roundTrip.ideas[0].assignee).toBe("Claude::Subagent101::Worktree12");
    expect(roundTrip.boardCards[0].assignee).toBe(
      "Claude::Subagent101::Worktree12",
    );
  });

  it("requires confirmation and replaces a project while retaining its identity", async () => {
    const bundle = await createTransferBundle();
    await resetDatabase(pool);
    await initializeDatabase(pool);
    const destination = await store.createProject({
      key: "APP",
      title: "Old destination",
    });
    await store.createIdea(admin, {
      projectId: destination.id,
      title: "Old destination task",
    });
    await store.createApiToken({
      name: "Destination token",
      projectIds: [destination.id],
    });

    await expect(
      store.importProject(bundle, { replaceExisting: false }),
    ).rejects.toThrow("replacement confirmation is required");

    const replaced = await store.importProject(bundle, {
      replaceExisting: true,
    });
    const importedIdeas = await store.listIdeas(admin, destination.id);

    expect(replaced.replaced).toBe(true);
    expect(replaced.item).toMatchObject({
      id: destination.id,
      key: "APP",
      title: bundle.project.title,
    });
    expect(importedIdeas.map((idea) => idea.title)).not.toContain(
      "Old destination task",
    );
    expect(importedIdeas.map((idea) => idea.projectId)).toEqual(
      bundle.ideas.map(() => destination.id),
    );
    expect(
      (await store.listApiTokens())[0].projects.map((item) => item.id),
    ).toContain(destination.id);
  });

  it("rejects child ID collisions with another project before replacement", async () => {
    const bundle = await createTransferBundle();
    await resetDatabase(pool);
    await initializeDatabase(pool);
    const destination = await store.createProject({
      key: "APP",
      title: "Old destination",
    });
    await store.createIdea(admin, {
      projectId: destination.id,
      title: "Old destination task",
    });
    const other = await store.createProject({ key: "OTH", title: "Other" });
    const unrelated = await store.createIdea(admin, {
      projectId: other.id,
      title: "Unrelated task",
    });
    const replacedId = bundle.ideas[0].id;
    const remapId = (id: string) => (id === replacedId ? unrelated.id : id);
    const collisionBundle: ProjectBundle = {
      ...bundle,
      ideas: bundle.ideas.map((idea) => ({
        ...idea,
        id: remapId(idea.id),
        dependsOn: idea.dependsOn.map(remapId),
        blocks: idea.blocks.map(remapId),
        blockedBy: idea.blockedBy.map(remapId),
      })),
      boardCards: bundle.boardCards.map((card) => ({
        ...card,
        ideaId: card.ideaId ? remapId(card.ideaId) : undefined,
        dependsOn: card.dependsOn.map(remapId),
        blockedBy: card.blockedBy.map(remapId),
      })),
      readinessEvents: bundle.readinessEvents.map((event) => ({
        ...event,
        ideaId: remapId(event.ideaId),
      })),
      documents: bundle.documents.map((document) => ({
        ...document,
        linkedIdeaIds: document.linkedIdeaIds.map(remapId),
      })),
    };

    await expect(
      store.importProject(collisionBundle, { replaceExisting: true }),
    ).rejects.toThrow(
      `Idea ID is already used by another project: ${unrelated.id}`,
    );
    expect(
      (await store.listIdeas(admin, destination.id)).map((idea) => idea.title),
    ).toContain("Old destination task");
  });

  it("rolls back replacement when an insertion fails", async () => {
    const bundle = await createTransferBundle();
    await resetDatabase(pool);
    await initializeDatabase(pool);
    const destination = await store.createProject({
      key: "APP",
      title: "Old destination",
    });
    await store.createIdea(admin, {
      projectId: destination.id,
      title: "Old destination task",
    });
    await pool.query(`
      create or replace function reject_project_import_test() returns trigger as $$
      begin
        if new.title = 'Force transaction failure' then
          raise exception 'forced project import failure';
        end if;
        return new;
      end;
      $$ language plpgsql;
      create trigger reject_project_import_test
      before insert on ideas
      for each row execute function reject_project_import_test();
    `);
    const failingBundle: ProjectBundle = {
      ...bundle,
      ideas: bundle.ideas.map((idea, index) =>
        index === 0 ? { ...idea, title: "Force transaction failure" } : idea,
      ),
    };

    try {
      await expect(
        store.importProject(failingBundle, { replaceExisting: true }),
      ).rejects.toThrow("forced project import failure");
      expect(
        (await store.listIdeas(admin, destination.id)).map(
          (idea) => idea.title,
        ),
      ).toContain("Old destination task");
    } finally {
      await pool.query(
        "drop trigger if exists reject_project_import_test on ideas",
      );
      await pool.query("drop function if exists reject_project_import_test()");
    }
  });
});
