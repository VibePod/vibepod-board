import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { importBoardJsonFile, normalizeImportedBoardData } from "../src/server/jsonImport.js";
import { adminAccess } from "../src/server/store.js";
import type { PostgresBoardStore } from "../src/server/storage.js";
import { closeTestPool, createTestStore } from "./helpers/store.js";

let tempDir: string;
let pool: Pool;
let store: PostgresBoardStore;
const admin = adminAccess("admin");

beforeEach(async () => {
  tempDir = await mkdtemp(join(tmpdir(), "vibepod-board-import-"));
  const created = await createTestStore();
  pool = created.pool;
  store = created.store;
});

afterEach(async () => {
  await closeTestPool(pool);
  await rm(tempDir, { recursive: true, force: true });
});

describe("JSON import", () => {
  it("normalizes legacy unowned work into a default project", () => {
    const timestamp = "2026-01-01T00:00:00.000Z";
    const normalized = normalizeImportedBoardData({
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
    });

    expect(normalized.schemaVersion).toBe(3);
    expect(normalized.projects[0].title).toBe("General");
    expect(normalized.ideas[0].projectId).toBe(normalized.projects[0].id);
    expect(normalized.ideas[0].taskNumber).toBe(1);
    expect(normalized.boardCards[0].projectId).toBe(normalized.projects[0].id);
    expect(normalized.documents[0].projectId).toBe(normalized.projects[0].id);
  });

  it("imports JSON into an empty PostgreSQL database", async () => {
    const timestamp = "2026-01-01T00:00:00.000Z";
    const filePath = join(tempDir, "board.json");
    await writeFile(
      filePath,
      `${JSON.stringify({
        schemaVersion: 3,
        projects: [
          {
            id: "project-1",
            key: "LS",
            title: "Launch site",
            summary: "Coordinate launch",
            createdAt: timestamp,
            updatedAt: timestamp
          }
        ],
        ideas: [
          {
            id: "idea-1",
            projectId: "project-1",
            taskNumber: 1,
            title: "Publish",
            summary: "",
            details: "",
            status: "ready",
            labels: ["launch"],
            acceptanceCriteria: [],
            createdAt: timestamp,
            updatedAt: timestamp
          }
        ],
        boardCards: [],
        documents: [],
        activity: []
      })}\n`,
      "utf8"
    );

    await importBoardJsonFile(pool, filePath);

    expect((await store.listProjects(admin))[0].id).toBe("project-1");
    expect((await store.listIdeas(admin, "project-1"))[0].title).toBe("Publish");
  });

  it("refuses to import over existing board data", async () => {
    await store.createProject({ key: "EX", title: "Existing" });
    const filePath = join(tempDir, "empty-board.json");
    await writeFile(filePath, JSON.stringify({ schemaVersion: 3 }), "utf8");

    await expect(importBoardJsonFile(pool, filePath)).rejects.toThrow(
      "PostgreSQL board tables are not empty"
    );
  });
});
