import { type BoardColumns, boardColumns, type Idea } from "../shared/types.js";
import { formatTaskId } from "./taskIdentity.js";

export const isIdeaOnBoard = (idea: Idea, columns: BoardColumns): boolean =>
  boardColumns.some((column) =>
    (columns[column] ?? []).some((card) => card.ideaId === idea.id),
  );

/**
 * `archivedIdeaIds` lists tasks whose card was archived: such a task is still on
 * the board as far as readiness goes, it only left the columns.
 */
export const taskListCardView = (
  idea: Idea,
  columns: BoardColumns,
  projectKey: string,
  archivedIdeaIds: ReadonlySet<string> = new Set(),
) => ({
  id: idea.id,
  taskId: formatTaskId(projectKey, idea.taskNumber),
  title: idea.title,
  status: idea.status,
  labels: [...idea.labels],
  isReady: archivedIdeaIds.has(idea.id) || isIdeaOnBoard(idea, columns),
  isArchived: archivedIdeaIds.has(idea.id),
  isBlocked: idea.blockedBy.length > 0,
  blockedByCount: idea.blockedBy.length,
});

export const readinessColor = (score: number): "red" | "yellow" | "green" => {
  if (score <= 3) {
    return "red";
  }
  if (score <= 6) {
    return "yellow";
  }
  return "green";
};

export const isReadinessStale = (card: {
  updatedAt: string;
  readinessEvaluatedAt?: string;
}): boolean =>
  card.readinessEvaluatedAt !== undefined &&
  card.updatedAt > card.readinessEvaluatedAt;

export const isCardReadinessStale = (
  card: { updatedAt: string; readinessEvaluatedAt?: string; ideaId?: string },
  ideaById: Map<string, { updatedAt: string }>,
): boolean => {
  if (card.readinessEvaluatedAt === undefined) {
    return false;
  }
  const linkedIdea = card.ideaId ? ideaById.get(card.ideaId) : undefined;
  const contentUpdatedAt = linkedIdea?.updatedAt ?? card.updatedAt;
  return contentUpdatedAt > card.readinessEvaluatedAt;
};
