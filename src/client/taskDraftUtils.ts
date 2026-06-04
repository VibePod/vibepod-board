import type { Idea, IdeaStatus } from "../shared/types.js";
import { formatListField, parseListField } from "./formUtils.js";
import { formatTaskId } from "./taskIdentity.js";

export type TaskDraft = {
  id?: string;
  taskId?: string;
  title: string;
  description: string;
  labels: string[];
  acceptanceCriteria: string;
  status: IdeaStatus;
};

export const emptyTaskDraft = (): TaskDraft => ({
  title: "",
  description: "",
  labels: [],
  acceptanceCriteria: "",
  status: "idea"
});

export const taskToDraft = (idea: Idea, projectKey?: string): TaskDraft => ({
  id: idea.id,
  ...(projectKey ? { taskId: formatTaskId(projectKey, idea.taskNumber) } : {}),
  title: idea.title,
  description: [idea.summary.trim(), idea.details.trim()]
    .filter((part, index, parts) => part && parts.indexOf(part) === index)
    .join("\n\n"),
  labels: [...idea.labels],
  acceptanceCriteria: formatListField(idea.acceptanceCriteria),
  status: idea.status
});

export const taskDraftToIdeaPayload = (draft: TaskDraft) => ({
  title: draft.title,
  summary: "",
  details: draft.description,
  labels: [...draft.labels],
  acceptanceCriteria: parseListField(draft.acceptanceCriteria)
});
