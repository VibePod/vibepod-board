import { type BoardColumns, boardColumns, type Idea } from "../shared/types.js";
import { formatTaskId } from "./taskIdentity.js";

export const isIdeaOnBoard = (idea: Idea, columns: BoardColumns): boolean =>
  boardColumns.some((column) =>
    (columns[column] ?? []).some((card) => card.ideaId === idea.id),
  );

export const taskListCardView = (
  idea: Idea,
  columns: BoardColumns,
  projectKey: string,
) => ({
  id: idea.id,
  taskId: formatTaskId(projectKey, idea.taskNumber),
  title: idea.title,
  status: idea.status,
  labels: [...idea.labels],
  isReady: isIdeaOnBoard(idea, columns),
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

const listing = (items: string[]): string =>
  items.length <= 1
    ? (items[0] ?? "")
    : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;

/**
 * What happens to a task once it is planned: a worker picks it up, or it waits
 * for the dependencies that are not finished yet.
 */
export const plannedNotice = (task: string, waitingFor: string[]): string =>
  waitingFor.length === 0
    ? `Planned ${task}: a worker will pick it up.`
    : `Planned ${task}; it waits for ${listing(waitingFor)}.`;
