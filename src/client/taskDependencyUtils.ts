import { dependencyMap, wouldCreateCycle } from "../shared/dependencies.js";
import type { Idea, IdeaStatus } from "../shared/types.js";
import { formatTaskId } from "./taskIdentity.js";

export type DependencyOption = {
  value: string;
  label: string;
};

export type DependencyLink = {
  id: string;
  taskId: string;
  title: string;
  status: IdeaStatus;
  isBlocking: boolean;
};

export const dependencyLabel = (idea: Idea, projectKey: string) =>
  `${formatTaskId(projectKey, idea.taskNumber)} · ${idea.title}`;

/**
 * Tasks that may be picked as blockers: same project, not the task itself, and
 * not already depending on it — those would close a cycle and be rejected.
 */
export const dependencyOptions = (
  tasks: Idea[],
  projectKey: string,
  taskId?: string,
): DependencyOption[] => {
  const edges = dependencyMap(tasks);
  return tasks
    .filter(
      (task) =>
        task.id !== taskId &&
        (!taskId || !wouldCreateCycle(edges, taskId, task.id)),
    )
    .sort((a, b) => a.taskNumber - b.taskNumber)
    .map((task) => ({
      value: task.id,
      label: dependencyLabel(task, projectKey),
    }));
};

export const dependencyLinks = (
  ids: string[],
  tasksById: Map<string, Idea>,
  projectKey: string,
  blockingIds: string[] = [],
): DependencyLink[] => {
  const blocking = new Set(blockingIds);
  return ids
    .map((id) => tasksById.get(id))
    .filter((task): task is Idea => Boolean(task))
    .sort((a, b) => a.taskNumber - b.taskNumber)
    .map((task) => ({
      id: task.id,
      taskId: formatTaskId(projectKey, task.taskNumber),
      title: task.title,
      status: task.status,
      isBlocking: blocking.has(task.id),
    }));
};
