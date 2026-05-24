import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";

import {
  boardColumns,
  type ActivityEvent,
  type BoardCard,
  type BoardColumn,
  type BoardColumns,
  type BoardData,
  type CreateBoardCardOptions,
  type CreateDocumentInput,
  type CreateIdeaInput,
  type Idea,
  type PlanDocument,
  type UpdateDocumentInput,
  type UpdateIdeaInput
} from "../shared/types.js";

const emptyData = (): BoardData => ({
  schemaVersion: 1,
  ideas: [],
  boardCards: [],
  documents: [],
  activity: []
});

const nowIso = () => new Date().toISOString();

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

const normalizeList = (items: string[] | undefined): string[] =>
  [...new Set((items ?? []).map((item) => item.trim()).filter(Boolean))];

const assertTitle = (title: string | undefined, entity: string) => {
  if (!title?.trim()) {
    throw new Error(`${entity} title is required`);
  }
};

export class BoardStore {
  private data: BoardData;

  constructor(private readonly filePath: string) {
    this.data = this.load();
  }

  getState(): BoardData {
    return clone(this.data);
  }

  listIdeas(): Idea[] {
    return clone(this.data.ideas).sort(sortUpdatedDesc);
  }

  createIdea(input: CreateIdeaInput): Idea {
    assertTitle(input.title, "Idea");
    const timestamp = nowIso();
    const idea: Idea = {
      id: randomUUID(),
      title: input.title.trim(),
      summary: input.summary?.trim() ?? "",
      details: input.details?.trim() ?? "",
      status: "idea",
      labels: normalizeList(input.labels),
      acceptanceCriteria: normalizeList(input.acceptanceCriteria),
      createdAt: timestamp,
      updatedAt: timestamp
    };

    this.data.ideas.push(idea);
    this.addActivity("idea.created", `Created idea: ${idea.title}`, timestamp);
    this.save();
    return clone(idea);
  }

  updateIdea(id: string, input: UpdateIdeaInput): Idea {
    const idea = this.requireIdea(id);
    if (input.title !== undefined) {
      assertTitle(input.title, "Idea");
      idea.title = input.title.trim();
    }
    if (input.summary !== undefined) {
      idea.summary = input.summary.trim();
    }
    if (input.details !== undefined) {
      idea.details = input.details.trim();
      if (idea.status === "idea") {
        idea.status = "refining";
      }
    }
    if (input.labels !== undefined) {
      idea.labels = normalizeList(input.labels);
    }
    if (input.acceptanceCriteria !== undefined) {
      idea.acceptanceCriteria = normalizeList(input.acceptanceCriteria);
      if (idea.status === "idea") {
        idea.status = "refining";
      }
    }
    if (input.status !== undefined) {
      idea.status = input.status;
    }

    idea.updatedAt = nowIso();
    this.addActivity("idea.updated", `Updated idea: ${idea.title}`);
    this.save();
    return clone(idea);
  }

  markIdeaReady(id: string): Idea {
    const idea = this.requireIdea(id);
    idea.status = "ready";
    idea.updatedAt = nowIso();
    this.addActivity("idea.ready", `Marked idea ready: ${idea.title}`);
    this.save();
    return clone(idea);
  }

  createBoardCardFromIdea(id: string, options: CreateBoardCardOptions): BoardCard {
    const idea = this.requireIdea(id);
    if (idea.status !== "ready" && idea.status !== "synced") {
      throw new Error("Only ready ideas can be moved to the board");
    }

    const existing = this.data.boardCards.find((card) => card.ideaId === idea.id);
    if (existing) {
      this.applyGitHubIssue(existing, options);
      this.applyGitHubIssue(idea, options);
      existing.updatedAt = nowIso();
      idea.updatedAt = existing.updatedAt;
      this.save();
      return clone(existing);
    }

    const timestamp = nowIso();
    const card: BoardCard = {
      id: randomUUID(),
      title: idea.title,
      details: idea.details || idea.summary,
      column: "ready",
      ideaId: idea.id,
      labels: [...idea.labels],
      createdAt: timestamp,
      updatedAt: timestamp
    };
    this.applyGitHubIssue(card, options);
    this.applyGitHubIssue(idea, options);

    this.data.boardCards.push(card);
    this.addActivity("board.created", `Created board card: ${card.title}`, timestamp);
    this.save();
    return clone(card);
  }

  listBoardCards(): BoardCard[] {
    return clone(this.data.boardCards).sort(sortUpdatedDesc);
  }

  getBoardColumns(): BoardColumns {
    const columns: BoardColumns = {
      ready: [],
      planned: [],
      in_progress: [],
      review: [],
      done: []
    };
    for (const card of this.data.boardCards) {
      columns[card.column].push(clone(card));
    }
    for (const column of boardColumns) {
      columns[column].sort(sortUpdatedDesc);
    }
    return columns;
  }

  moveBoardCard(id: string, column: BoardColumn): BoardCard {
    const card = this.requireBoardCard(id);
    card.column = column;
    card.updatedAt = nowIso();
    this.addActivity("board.moved", `Moved card to ${column}: ${card.title}`);
    this.save();
    return clone(card);
  }

  listDocuments(): PlanDocument[] {
    return clone(this.data.documents).sort(sortUpdatedDesc);
  }

  createDocument(input: CreateDocumentInput): PlanDocument {
    assertTitle(input.title, "Document");
    const timestamp = nowIso();
    const document: PlanDocument = {
      id: randomUUID(),
      title: input.title.trim(),
      kind: input.kind ?? "execution_plan",
      content: input.content?.trim() ?? "",
      linkedIdeaIds: normalizeList(input.linkedIdeaIds),
      linkedCardIds: normalizeList(input.linkedCardIds),
      createdAt: timestamp,
      updatedAt: timestamp
    };

    this.data.documents.push(document);
    this.addActivity("document.created", `Created document: ${document.title}`, timestamp);
    this.save();
    return clone(document);
  }

  updateDocument(id: string, input: UpdateDocumentInput): PlanDocument {
    const document = this.requireDocument(id);
    if (input.title !== undefined) {
      assertTitle(input.title, "Document");
      document.title = input.title.trim();
    }
    if (input.kind !== undefined) {
      document.kind = input.kind;
    }
    if (input.content !== undefined) {
      document.content = input.content;
    }
    if (input.linkedIdeaIds !== undefined) {
      document.linkedIdeaIds = normalizeList(input.linkedIdeaIds);
    }
    if (input.linkedCardIds !== undefined) {
      document.linkedCardIds = normalizeList(input.linkedCardIds);
    }

    document.updatedAt = nowIso();
    this.addActivity("document.updated", `Updated document: ${document.title}`);
    this.save();
    return clone(document);
  }

  listActivity(): ActivityEvent[] {
    return clone(this.data.activity).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  private load(): BoardData {
    mkdirSync(dirname(this.filePath), { recursive: true });
    if (!existsSync(this.filePath)) {
      return emptyData();
    }

    const parsed = JSON.parse(readFileSync(this.filePath, "utf8")) as Partial<BoardData>;
    return {
      schemaVersion: 1,
      ideas: parsed.ideas ?? [],
      boardCards: parsed.boardCards ?? [],
      documents: parsed.documents ?? [],
      activity: parsed.activity ?? []
    };
  }

  private save() {
    mkdirSync(dirname(this.filePath), { recursive: true });
    writeFileSync(this.filePath, `${JSON.stringify(this.data, null, 2)}\n`, "utf8");
  }

  private requireIdea(id: string): Idea {
    const idea = this.data.ideas.find((item) => item.id === id);
    if (!idea) {
      throw new Error(`Idea not found: ${id}`);
    }
    return idea;
  }

  private requireBoardCard(id: string): BoardCard {
    const card = this.data.boardCards.find((item) => item.id === id);
    if (!card) {
      throw new Error(`Board card not found: ${id}`);
    }
    return card;
  }

  private requireDocument(id: string): PlanDocument {
    const document = this.data.documents.find((item) => item.id === id);
    if (!document) {
      throw new Error(`Document not found: ${id}`);
    }
    return document;
  }

  private applyGitHubIssue(
    entity: Pick<Idea | BoardCard, "githubIssueUrl" | "githubIssueNumber"> & Partial<Idea>,
    options: CreateBoardCardOptions
  ) {
    if (options.githubMode === "github") {
      entity.githubIssueUrl = options.githubIssueUrl;
      entity.githubIssueNumber = options.githubIssueNumber;
      if ("status" in entity) {
        entity.status = "synced";
      }
    }
  }

  private addActivity(type: string, message: string, createdAt = nowIso()) {
    this.data.activity.push({
      id: randomUUID(),
      type,
      message,
      createdAt
    });
    this.data.activity = this.data.activity.slice(-100);
  }
}

const sortUpdatedDesc = <T extends { updatedAt: string }>(a: T, b: T) =>
  b.updatedAt.localeCompare(a.updatedAt);
