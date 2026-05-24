import { mkdtemp, rm } from "node:fs/promises";
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
