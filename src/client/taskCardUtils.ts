import { boardColumns, type BoardColumns, type Idea } from "../shared/types.js";
import { formatTaskId } from "./taskIdentity.js";

export const isIdeaOnBoard = (idea: Idea, columns: BoardColumns): boolean =>
  boardColumns.some((column) => (columns[column] ?? []).some((card) => card.ideaId === idea.id));

export const taskListCardView = (idea: Idea, columns: BoardColumns, projectKey: string) => ({
  id: idea.id,
  taskId: formatTaskId(projectKey, idea.taskNumber),
  title: idea.title,
  status: idea.status,
  labels: [...idea.labels],
  isReady: isIdeaOnBoard(idea, columns)
});
