import type { Idea } from "../shared/types.js";
import { formatTaskId } from "./taskIdentity.js";

export const taskOverviewForIdea = (idea: Idea, projectKey: string) => ({
  taskId: formatTaskId(projectKey, idea.taskNumber),
  title: idea.title,
  description: idea.details.trim() || idea.summary.trim() || "No description yet.",
  status: idea.status,
  labels: [...idea.labels],
  acceptanceCriteria: [...idea.acceptanceCriteria]
});
