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
  type CreateProjectInput,
  type Idea,
  type PlanDocument,
  type Project,
  type UpdateDocumentInput,
  type UpdateIdeaInput,
  type UpdateProjectInput
} from "../shared/types.js";

const emptyData = (): BoardData => ({
  schemaVersion: 3,
  projects: [],
  ideas: [],
  boardCards: [],
  documents: [],
  activity: []
});

type LegacyIdea = Omit<Idea, "projectId" | "taskNumber"> & { projectId?: string; taskNumber?: number };
type LegacyBoardCard = Omit<BoardCard, "projectId"> & { projectId?: string };
type LegacyPlanDocument = Omit<PlanDocument, "projectId"> & { projectId?: string };
type PersistedBoardData = Partial<
  Omit<BoardData, "schemaVersion" | "projects" | "ideas" | "boardCards" | "documents">
> & {
  schemaVersion?: number;
  projects?: Partial<Project>[];
  ideas?: LegacyIdea[];
  boardCards?: LegacyBoardCard[];
  documents?: LegacyPlanDocument[];
};

type LoadResult = {
  data: BoardData;
  migrated: boolean;
};

const defaultProjectSummary = "Default project for uncategorized work.";
const projectKeyPattern = /^[A-Z]{1,3}$/;

const nowIso = () => new Date().toISOString();

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

const normalizeList = (items: string[] | undefined): string[] =>
  [...new Set((items ?? []).map((item) => item.trim()).filter(Boolean))];

const assertTitle = (title: string | undefined, entity: string) => {
  if (!title?.trim()) {
    throw new Error(`${entity} title is required`);
  }
};

const normalizeProjectKey = (key: string | undefined): string => {
  if (!key?.trim()) {
    throw new Error("Project ID is required");
  }
  const normalized = key.trim();
  if (!projectKeyPattern.test(normalized)) {
    throw new Error("Project ID must be 1 to 3 capital letters");
  }
  return normalized;
};

export class BoardStore {
  private data: BoardData;

  constructor(private readonly filePath: string) {
    const { data, migrated } = this.load();
    this.data = data;
    if (migrated) {
      this.save();
    }
  }

  getState(): BoardData {
    return clone(this.data);
  }

  listProjects(): Project[] {
    return clone(this.data.projects).sort(sortUpdatedDesc);
  }

  createProject(input: CreateProjectInput): Project {
    assertTitle(input.title, "Project");
    const key = normalizeProjectKey(input.key);
    this.assertProjectKeyAvailable(key);
    const timestamp = nowIso();
    const project: Project = {
      id: randomUUID(),
      key,
      title: input.title.trim(),
      summary: input.summary?.trim() ?? "",
      createdAt: timestamp,
      updatedAt: timestamp
    };

    this.data.projects.push(project);
    this.addActivity("project.created", `Created project: ${project.title}`, timestamp);
    this.save();
    return clone(project);
  }

  updateProject(id: string, input: UpdateProjectInput): Project {
    const project = this.requireProject(id);
    if (input.key !== undefined) {
      const key = normalizeProjectKey(input.key);
      this.assertProjectKeyAvailable(key, project.id);
      project.key = key;
    }
    if (input.title !== undefined) {
      assertTitle(input.title, "Project");
      project.title = input.title.trim();
    }
    if (input.summary !== undefined) {
      project.summary = input.summary.trim();
    }

    project.updatedAt = nowIso();
    this.addActivity("project.updated", `Updated project: ${project.title}`);
    this.save();
    return clone(project);
  }

  listIdeas(projectId?: string): Idea[] {
    const filtered = projectId
      ? this.data.ideas.filter((idea) => idea.projectId === this.requireProject(projectId).id)
      : this.data.ideas;
    return clone(filtered).sort(sortUpdatedDesc);
  }

  createIdea(input: CreateIdeaInput): Idea {
    assertTitle(input.title, "Idea");
    const projectId = this.resolveProjectId(input.projectId);
    const timestamp = nowIso();
    const idea: Idea = {
      id: randomUUID(),
      projectId,
      taskNumber: this.nextTaskNumber(projectId),
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
    return this.setIdeaBoardAvailability(id, true);
  }

  setIdeaBoardAvailability(id: string, available: boolean): Idea {
    const idea = this.requireIdea(id);
    const timestamp = nowIso();

    if (available) {
      idea.status = "ready";
      idea.updatedAt = timestamp;
      this.ensureBoardCard(idea, { githubMode: "local" }, timestamp);
      this.addActivity("idea.ready", `Marked idea ready: ${idea.title}`, timestamp);
      this.save();
      return clone(idea);
    }

    this.data.boardCards = this.data.boardCards.filter((card) => card.ideaId !== idea.id);
    idea.status = idea.details || idea.acceptanceCriteria.length > 0 ? "refining" : "idea";
    idea.updatedAt = timestamp;
    this.addActivity("idea.not_ready", `Removed idea from board: ${idea.title}`, timestamp);
    this.save();
    return clone(idea);
  }

  createBoardCardFromIdea(id: string, options: CreateBoardCardOptions): BoardCard {
    const idea = this.requireIdea(id);
    if (idea.status !== "ready") {
      throw new Error("Only ready ideas can be moved to the board");
    }

    const existing = this.ensureBoardCard(idea, options);
    this.save();
    return clone(existing);
  }

  private ensureBoardCard(idea: Idea, options: CreateBoardCardOptions, timestamp = nowIso()): BoardCard {
    const existing = this.data.boardCards.find((card) => card.ideaId === idea.id);
    if (existing) {
      this.applyGitHubIssue(existing, options);
      this.applyGitHubIssue(idea, options);
      existing.updatedAt = timestamp;
      idea.updatedAt = existing.updatedAt;
      return existing;
    }

    const card: BoardCard = {
      id: randomUUID(),
      projectId: idea.projectId,
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
    return card;
  }

  listBoardCards(projectId?: string): BoardCard[] {
    const filtered = projectId
      ? this.data.boardCards.filter((card) => card.projectId === this.requireProject(projectId).id)
      : this.data.boardCards;
    return clone(filtered).sort(sortUpdatedDesc);
  }

  getBoardColumns(projectId?: string): BoardColumns {
    const scopedProjectId = projectId ? this.requireProject(projectId).id : undefined;
    const columns: BoardColumns = {
      ready: [],
      planned: [],
      in_progress: [],
      review: [],
      done: []
    };
    for (const card of this.data.boardCards) {
      if (scopedProjectId && card.projectId !== scopedProjectId) {
        continue;
      }
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

  listDocuments(projectId?: string): PlanDocument[] {
    const filtered = projectId
      ? this.data.documents.filter((document) => document.projectId === this.requireProject(projectId).id)
      : this.data.documents;
    return clone(filtered).sort(sortUpdatedDesc);
  }

  createDocument(input: CreateDocumentInput): PlanDocument {
    assertTitle(input.title, "Document");
    const projectId = this.resolveProjectId(input.projectId);
    const timestamp = nowIso();
    const document: PlanDocument = {
      id: randomUUID(),
      projectId,
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

  private load(): LoadResult {
    mkdirSync(dirname(this.filePath), { recursive: true });
    if (!existsSync(this.filePath)) {
      return { data: emptyData(), migrated: false };
    }

    const parsed = JSON.parse(readFileSync(this.filePath, "utf8")) as PersistedBoardData;
    const normalizedProjects = normalizeProjects(parsed.projects);
    const data: BoardData = {
      schemaVersion: 3,
      projects: normalizedProjects.projects,
      ideas: (parsed.ideas ?? []) as Idea[],
      boardCards: (parsed.boardCards ?? []) as BoardCard[],
      documents: (parsed.documents ?? []) as PlanDocument[],
      activity: parsed.activity ?? []
    };

    const ownershipMigrated = migrateProjectOwnership(data);
    const statusMigrated = normalizeIdeaStatuses(data);
    const taskNumberMigrated = normalizeIdeaTaskNumbers(data);
    const migrated =
      parsed.schemaVersion !== 3 ||
      normalizedProjects.migrated ||
      ownershipMigrated ||
      statusMigrated ||
      taskNumberMigrated;

    return { data, migrated };
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

  private requireProject(id: string): Project {
    const project = this.data.projects.find((item) => item.id === id);
    if (!project) {
      throw new Error(`Project not found: ${id}`);
    }
    return project;
  }

  private resolveProjectId(id: string | undefined): string {
    if (id !== undefined) {
      return this.requireProject(id).id;
    }

    const existing = this.data.projects[0];
    if (existing) {
      return existing.id;
    }

    const timestamp = nowIso();
    const project: Project = {
      id: randomUUID(),
      key: nextAvailableProjectKey(this.data.projects.map((item) => item.key), "GEN"),
      title: "General",
      summary: defaultProjectSummary,
      createdAt: timestamp,
      updatedAt: timestamp
    };
    this.data.projects.push(project);
    this.addActivity("project.created", `Created project: ${project.title}`, timestamp);
    return project.id;
  }

  private assertProjectKeyAvailable(key: string, currentProjectId?: string) {
    const existing = this.data.projects.find(
      (project) => project.key === key && project.id !== currentProjectId
    );
    if (existing) {
      throw new Error(`Project ID ${key} is already used`);
    }
  }

  private nextTaskNumber(projectId: string): number {
    return (
      this.data.ideas
        .filter((idea) => idea.projectId === projectId)
        .reduce((highest, idea) => Math.max(highest, idea.taskNumber ?? 0), 0) + 1
    );
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

const sortUpdatedDesc = <T extends { updatedAt: string; title?: string }>(a: T, b: T) =>
  b.updatedAt.localeCompare(a.updatedAt) || (a.title ?? "").localeCompare(b.title ?? "");

const normalizeProjects = (projects: Partial<Project>[] | undefined) => {
  let migrated = false;
  const normalized: Project[] = [];
  const usedKeys = new Set<string>();
  for (const project of projects ?? []) {
    if (!project.id || !project.title?.trim()) {
      migrated = true;
      continue;
    }
    const timestamp = project.updatedAt ?? project.createdAt ?? nowIso();
    const requestedKey = normalizedProjectKeyOrUndefined(project.key);
    const key =
      requestedKey && !usedKeys.has(requestedKey)
        ? requestedKey
        : nextAvailableProjectKey(usedKeys, projectKeyFromTitle(project.title));
    usedKeys.add(key);
    normalized.push({
      id: project.id,
      key,
      title: project.title.trim(),
      summary: project.summary?.trim() ?? "",
      createdAt: project.createdAt ?? timestamp,
      updatedAt: project.updatedAt ?? timestamp
    });
    if (
      !project.createdAt ||
      !project.updatedAt ||
      project.title !== project.title.trim() ||
      project.summary !== project.summary?.trim() ||
      project.key !== key
    ) {
      migrated = true;
    }
  }
  return { projects: normalized, migrated };
};

const migrateProjectOwnership = (data: BoardData) => {
  let migrated = false;
  const hasUnownedWork =
    data.ideas.some((idea) => !idea.projectId) ||
    data.boardCards.some((card) => !card.projectId) ||
    data.documents.some((document) => !document.projectId);

  if (hasUnownedWork && data.projects.length === 0) {
    const timestamp = nowIso();
    data.projects.push({
      id: randomUUID(),
      key: nextAvailableProjectKey(data.projects.map((project) => project.key), "GEN"),
      title: "General",
      summary: defaultProjectSummary,
      createdAt: timestamp,
      updatedAt: timestamp
    });
    migrated = true;
  }

  const fallbackProjectId = data.projects[0]?.id;
  if (!fallbackProjectId) {
    return migrated;
  }

  const ideaProjectIds = new Map(data.ideas.map((idea) => [idea.id, idea.projectId || fallbackProjectId]));
  const cardProjectIds = new Map(
    data.boardCards.map((card) => [
      card.id,
      card.projectId || (card.ideaId ? ideaProjectIds.get(card.ideaId) : undefined) || fallbackProjectId
    ])
  );

  for (const idea of data.ideas) {
    if (!idea.projectId) {
      idea.projectId = fallbackProjectId;
      migrated = true;
    }
  }

  for (const card of data.boardCards) {
    if (!card.projectId) {
      card.projectId = cardProjectIds.get(card.id) ?? fallbackProjectId;
      migrated = true;
    }
  }

  for (const document of data.documents) {
    if (!document.projectId) {
      document.projectId =
        document.linkedIdeaIds.map((id) => ideaProjectIds.get(id)).find(Boolean) ??
        document.linkedCardIds.map((id) => cardProjectIds.get(id)).find(Boolean) ??
        fallbackProjectId;
      migrated = true;
    }
  }

  return migrated;
};

const normalizeIdeaTaskNumbers = (data: BoardData) => {
  let migrated = false;
  for (const project of data.projects) {
    const ideas = data.ideas
      .filter((idea) => idea.projectId === project.id)
      .sort(
        (a, b) =>
          a.createdAt.localeCompare(b.createdAt) ||
          a.updatedAt.localeCompare(b.updatedAt) ||
          a.id.localeCompare(b.id)
      );
    const usedNumbers = new Set<number>();
    let nextNumber = 1;

    for (const idea of ideas) {
      const currentNumber =
        Number.isInteger(idea.taskNumber) && idea.taskNumber > 0 ? idea.taskNumber : undefined;
      if (currentNumber && !usedNumbers.has(currentNumber)) {
        usedNumbers.add(currentNumber);
        nextNumber = Math.max(nextNumber, currentNumber + 1);
        continue;
      }

      while (usedNumbers.has(nextNumber)) {
        nextNumber += 1;
      }
      idea.taskNumber = nextNumber;
      usedNumbers.add(nextNumber);
      nextNumber += 1;
      migrated = true;
    }
  }
  return migrated;
};

const normalizeIdeaStatuses = (data: BoardData) => {
  let migrated = false;
  for (const idea of data.ideas) {
    if ((idea.status as string) === "synced") {
      idea.status = "ready";
      migrated = true;
    }
    if ((idea.status as string) === "dennied") {
      idea.status = "denied";
      migrated = true;
    }
  }
  return migrated;
};

const normalizedProjectKeyOrUndefined = (key: unknown): string | undefined => {
  if (typeof key !== "string") {
    return undefined;
  }
  const normalized = key.trim();
  return projectKeyPattern.test(normalized) ? normalized : undefined;
};

const projectKeyFromTitle = (title: string): string => {
  const letters = title
    .split(/\s+/)
    .map((part) => part.replace(/[^A-Za-z]/g, ""))
    .filter(Boolean)
    .map((part) => part[0].toUpperCase())
    .join("")
    .slice(0, 3);
  return letters || "PRJ";
};

const nextAvailableProjectKey = (usedKeysInput: Iterable<string | undefined>, preferred = "PRJ"): string => {
  const usedKeys = new Set<string>();
  for (const key of usedKeysInput) {
    const normalized = normalizedProjectKeyOrUndefined(key);
    if (normalized) {
      usedKeys.add(normalized);
    }
  }

  const preferredKey = normalizedProjectKeyOrUndefined(preferred) ?? "PRJ";
  if (!usedKeys.has(preferredKey)) {
    return preferredKey;
  }

  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  for (const first of alphabet) {
    if (!usedKeys.has(first)) {
      return first;
    }
  }
  for (const first of alphabet) {
    for (const second of alphabet) {
      const key = `${first}${second}`;
      if (!usedKeys.has(key)) {
        return key;
      }
    }
  }
  for (const first of alphabet) {
    for (const second of alphabet) {
      for (const third of alphabet) {
        const key = `${first}${second}${third}`;
        if (!usedKeys.has(key)) {
          return key;
        }
      }
    }
  }

  throw new Error("No available project IDs");
};
