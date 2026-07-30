import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { BoardStore } from "../src/server/storage.js";

let dir: string;
let store: BoardStore;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "board-readiness-"));
  store = new BoardStore(join(dir, "board.json"));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

const createCard = () => {
  const project = store.createProject({ key: "APP", title: "App" });
  const idea = store.createIdea({ projectId: project.id, title: "Card" });
  store.markIdeaReady(idea.id);
  return store.getBoardColumns(project.id).ready[0];
};

describe("BoardStore readiness", () => {
  it("sets readiness without bumping updatedAt and keeps it across edits", () => {
    const card = createCard();

    const scored = store.setCardReadiness(card.id, {
      score: 9,
      reason: "Fully specified",
    });
    expect(scored.readinessScore).toBe(9);
    expect(scored.readinessReason).toBe("Fully specified");
    expect(scored.readinessEvaluatedAt).toBeDefined();
    expect(scored.updatedAt).toBe(card.updatedAt);

    const edited = store.updateBoardCard(card.id, { details: "changed" });
    expect(edited.readinessScore).toBe(9);
  });

  it("validates score and reason", () => {
    const card = createCard();

    expect(() =>
      store.setCardReadiness(card.id, { score: 0, reason: "r" }),
    ).toThrow("Readiness score must be an integer from 1 to 10");
    expect(() =>
      store.setCardReadiness(card.id, { score: 5, reason: " " }),
    ).toThrow("Readiness reason is required");
  });

  it("sets idea readiness and mirrors it onto the linked card without bumping updatedAt", () => {
    const project = store.createProject({
      key: "IDR",
      title: "Idea readiness",
    });
    const idea = store.createIdea({
      projectId: project.id,
      title: "Scored idea",
    });
    store.markIdeaReady(idea.id);
    const cardBefore = store.getBoardColumns(project.id).ready[0];
    const ideaBefore = store
      .listIdeas(project.id)
      .find((i) => i.id === idea.id)!;

    const scored = store.setIdeaReadiness(idea.id, {
      score: 7,
      reason: "Clear scope",
    });
    expect(scored.readinessScore).toBe(7);
    expect(scored.readinessReason).toBe("Clear scope");
    expect(scored.readinessEvaluatedAt).toBeDefined();
    expect(scored.updatedAt).toBe(ideaBefore.updatedAt);

    const card = store.getBoardColumns(project.id).ready[0];
    expect(card.readinessScore).toBe(7);
    expect(card.readinessReason).toBe("Clear scope");
    expect(card.updatedAt).toBe(cardBefore.updatedAt);
  });

  it("validates idea readiness score and reason", () => {
    const project = store.createProject({ key: "IDV", title: "Idea validate" });
    const idea = store.createIdea({ projectId: project.id, title: "Idea" });
    expect(() =>
      store.setIdeaReadiness(idea.id, { score: 0, reason: "r" }),
    ).toThrow("Readiness score must be an integer from 1 to 10");
    expect(() =>
      store.setIdeaReadiness(idea.id, { score: 5, reason: " " }),
    ).toThrow("Readiness reason is required");
  });

  it("redirects card scoring to the linked idea", () => {
    const project = store.createProject({ key: "RDR", title: "Redirect" });
    const idea = store.createIdea({ projectId: project.id, title: "Linked" });
    store.markIdeaReady(idea.id);
    const card = store.getBoardColumns(project.id).ready[0];

    const scoredCard = store.setCardReadiness(card.id, {
      score: 4,
      reason: "Some risk",
    });
    expect(scoredCard.readinessScore).toBe(4);

    const updatedIdea = store
      .listIdeas(project.id)
      .find((i) => i.id === idea.id)!;
    expect(updatedIdea.readinessScore).toBe(4);
    expect(updatedIdea.readinessReason).toBe("Some risk");
  });

  it("appends a readiness event on every call and lists newest-first", () => {
    const project = store.createProject({ key: "HST", title: "History" });
    const idea = store.createIdea({ projectId: project.id, title: "Tracked" });

    store.setIdeaReadiness(idea.id, { score: 4, reason: "Rough" });
    store.setIdeaReadiness(idea.id, { score: 4, reason: "Rough" });
    store.setIdeaReadiness(idea.id, { score: 8, reason: "Refined" });

    const events = store.listIdeaReadiness(idea.id);
    expect(events).toHaveLength(3);
    expect(events[0].score).toBe(8);
    expect(events[0].reason).toBe("Refined");
    expect(events[1].score).toBe(4);
    expect(events.every((e) => e.ideaId === idea.id)).toBe(true);
    expect(events[0].createdAt >= events[2].createdAt).toBe(true);

    const updated = store.listIdeas(project.id).find((i) => i.id === idea.id)!;
    expect(updated.readinessScore).toBe(8);
  });

  it("scopes history to the idea", () => {
    const project = store.createProject({ key: "SCP", title: "Scope" });
    const a = store.createIdea({ projectId: project.id, title: "A" });
    const b = store.createIdea({ projectId: project.id, title: "B" });
    store.setIdeaReadiness(a.id, { score: 5, reason: "a" });
    store.setIdeaReadiness(b.id, { score: 6, reason: "b" });

    expect(store.listIdeaReadiness(a.id)).toHaveLength(1);
    expect(store.listIdeaReadiness(a.id)[0].reason).toBe("a");
  });

  it("carries idea readiness onto a card created at promotion time", () => {
    const project = store.createProject({ key: "CRY", title: "Carry" });
    const idea = store.createIdea({
      projectId: project.id,
      title: "Scored then promoted",
    });
    store.setIdeaReadiness(idea.id, { score: 8, reason: "Ready to ship" });

    store.markIdeaReady(idea.id);
    const card = store.getBoardColumns(project.id).ready[0];
    expect(card.readinessScore).toBe(8);
    expect(card.readinessReason).toBe("Ready to ship");
    expect(card.readinessEvaluatedAt).toBeDefined();
  });
});
