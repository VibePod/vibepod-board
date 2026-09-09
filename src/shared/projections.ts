import type {
  BoardCard,
  BoardColumn,
  DocumentKind,
  Idea,
  IdeaStatus,
  PlanDocument,
} from "./types.js";

export const viewLevels = ["ref", "compact", "full"] as const;
export type ViewLevel = (typeof viewLevels)[number];

export type EntityRef = {
  id: string;
  key?: string;
  updatedAt: string;
};

export type CompactIdea = {
  id: string;
  key?: string;
  projectId: string;
  taskNumber: number;
  title: string;
  status: IdeaStatus;
  labels: string[];
  column?: BoardColumn;
  assignee?: string;
  readinessScore?: number;
  dependsOn: string[];
  blockedBy: string[];
  detailsLength: number;
  acceptanceCriteriaCount: number;
  updatedAt: string;
};

export type CompactBoardCard = {
  id: string;
  key?: string;
  ideaId?: string;
  projectId: string;
  title: string;
  column: BoardColumn;
  branchName?: string;
  labels: string[];
  blockedBy: string[];
  assignee?: string;
  readinessScore?: number;
  detailsLength: number;
  updatedAt: string;
};

export type CompactDocument = {
  id: string;
  projectId: string;
  title: string;
  kind: DocumentKind;
  linkedIdeaIds: string[];
  contentLength: number;
  updatedAt: string;
};

/** Task id to human key, such as `idea-1` to `VP-236`. */
export type TaskKeys = Map<string, string>;

const named = (keys: TaskKeys | undefined, ids: string[]): string[] =>
  ids.map((id) => keys?.get(id) ?? id);

/**
 * Undefined values are dropped rather than written as own properties: a
 * projection is measured in bytes, and an explicit `"key": undefined` also
 * changes how the object compares.
 */
const withoutUndefined = <T extends object>(value: T): T =>
  Object.fromEntries(
    Object.entries(value).filter(([, entry]) => entry !== undefined),
  ) as T;

export const refIdea = (idea: Idea, keys?: TaskKeys): EntityRef =>
  withoutUndefined({
    id: idea.id,
    key: keys?.get(idea.id),
    updatedAt: idea.updatedAt,
  });

export const compactIdea = (
  idea: Idea,
  keys?: TaskKeys,
  column?: BoardColumn,
): CompactIdea =>
  withoutUndefined({
    id: idea.id,
    key: keys?.get(idea.id),
    projectId: idea.projectId,
    taskNumber: idea.taskNumber,
    title: idea.title,
    status: idea.status,
    labels: idea.labels,
    column,
    assignee: idea.assignee,
    readinessScore: idea.readinessScore,
    dependsOn: named(keys, idea.dependsOn),
    blockedBy: named(keys, idea.blockedBy),
    detailsLength: idea.details.length,
    acceptanceCriteriaCount: idea.acceptanceCriteria.length,
    updatedAt: idea.updatedAt,
  });

export const refBoardCard = (card: BoardCard, keys?: TaskKeys): EntityRef =>
  withoutUndefined({
    id: card.id,
    key: card.ideaId ? keys?.get(card.ideaId) : undefined,
    updatedAt: card.updatedAt,
  });

export const compactBoardCard = (
  card: BoardCard,
  keys?: TaskKeys,
): CompactBoardCard =>
  withoutUndefined({
    id: card.id,
    key: card.ideaId ? keys?.get(card.ideaId) : undefined,
    ideaId: card.ideaId,
    projectId: card.projectId,
    title: card.title,
    column: card.column,
    branchName: card.branchName,
    labels: card.labels,
    blockedBy: named(keys, card.blockedBy),
    assignee: card.assignee,
    readinessScore: card.readinessScore,
    detailsLength: card.details.length,
    updatedAt: card.updatedAt,
  });

export const compactDocument = (document: PlanDocument): CompactDocument => ({
  id: document.id,
  projectId: document.projectId,
  title: document.title,
  kind: document.kind,
  linkedIdeaIds: document.linkedIdeaIds,
  contentLength: document.content.length,
  updatedAt: document.updatedAt,
});
