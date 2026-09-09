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
  dependsOn: string[];
  repositoryLocalPath: string;
  repositoryRemoteUrl: string;
  assignee: string;
  status: IdeaStatus;
};

export const emptyTaskDraft = (): TaskDraft => ({
  title: "",
  description: "",
  labels: [],
  acceptanceCriteria: "",
  dependsOn: [],
  repositoryLocalPath: "",
  repositoryRemoteUrl: "",
  assignee: "",
  status: "idea",
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
  dependsOn: [...idea.dependsOn],
  repositoryLocalPath: idea.repositoryLocalPath ?? "",
  repositoryRemoteUrl: idea.repositoryRemoteUrl ?? "",
  assignee: idea.assignee ?? "",
  status: idea.status,
});

export const taskDraftToIdeaPayload = (draft: TaskDraft) => ({
  title: draft.title,
  summary: "",
  details: draft.description,
  labels: [...draft.labels],
  acceptanceCriteria: parseListField(draft.acceptanceCriteria),
  dependsOn: [...draft.dependsOn],
  repositoryLocalPath: draft.repositoryLocalPath,
  repositoryRemoteUrl: draft.repositoryRemoteUrl,
  assignee: draft.assignee,
});
