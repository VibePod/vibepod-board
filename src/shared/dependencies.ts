import type { BoardCard, BoardColumn, Idea, IdeaStatus } from "./types.js";

export type TaskGraphNode = {
  id: string;
  taskId: string;
  projectId: string;
  projectKey: string;
  taskNumber: number;
  title: string;
  status: IdeaStatus;
  column?: BoardColumn;
  cardId?: string;
  dependsOn: string[];
};

export type WorkOrderItem = {
  id: string;
  taskId: string;
  projectId: string;
  title: string;
  status: IdeaStatus;
  column?: BoardColumn;
  cardId?: string;
  position: number;
  wave: number;
  dependsOn: string[];
  blockedBy: string[];
  isBlocked: boolean;
  isComplete: boolean;
  isActionable: boolean;
};

export type TaskWorkOrder = {
  items: WorkOrderItem[];
  cyclicTaskIds: string[];
};

export const formatTaskKey = (projectKey: string, taskNumber: number) =>
  `${projectKey}-${taskNumber}`;

export const normalizeDependencyIds = (ids: string[] | undefined): string[] => [
  ...new Set((ids ?? []).map((id) => id.trim()).filter(Boolean)),
];

/**
 * A dependency stops blocking once the blocker reached the done column, or once
 * it was denied — denied work is never coming, so it must not wedge the graph.
 */
export const isTaskComplete = (task: {
  status: IdeaStatus;
  column?: BoardColumn;
}): boolean => task.column === "done" || task.status === "denied";

export const dependencyMap = (
  tasks: { id: string; dependsOn: string[] }[],
): Map<string, string[]> =>
  new Map(
    tasks.map((task) => [task.id, normalizeDependencyIds(task.dependsOn)]),
  );

/**
 * True when `taskId` is reachable from `startId` by following dependsOn edges,
 * i.e. `startId` already depends on `taskId` directly or transitively.
 */
export const dependsOnTransitively = (
  edges: Map<string, string[]>,
  startId: string,
  taskId: string,
): boolean => {
  const seen = new Set<string>();
  const queue = [startId];
  while (queue.length > 0) {
    const current = queue.shift() as string;
    if (current === taskId && current !== startId) {
      return true;
    }
    if (seen.has(current)) {
      continue;
    }
    seen.add(current);
    for (const next of edges.get(current) ?? []) {
      if (next === taskId) {
        return true;
      }
      queue.push(next);
    }
  }
  return false;
};

/** Adding "taskId depends on dependsOnId" closes a loop when the blocker already depends on the task. */
export const wouldCreateCycle = (
  edges: Map<string, string[]>,
  taskId: string,
  dependsOnId: string,
): boolean =>
  taskId === dependsOnId || dependsOnTransitively(edges, dependsOnId, taskId);

/** Task ids that cannot be topologically ordered because they sit in or behind a cycle. */
export const findCyclicTaskIds = (edges: Map<string, string[]>): string[] =>
  orderNodes(edges).cyclicTaskIds;

export const buildWorkOrder = (
  tasks: Pick<
    Idea,
    "id" | "projectId" | "taskNumber" | "title" | "status" | "dependsOn"
  >[],
  cards: Pick<BoardCard, "id" | "ideaId" | "column">[],
  projectKeys: Map<string, string>,
): TaskWorkOrder => {
  const cardByTaskId = new Map(
    cards
      .filter((card): card is typeof card & { ideaId: string } =>
        Boolean(card.ideaId),
      )
      .map((card) => [card.ideaId, card]),
  );
  const nodes: TaskGraphNode[] = tasks.map((task) => {
    const card = cardByTaskId.get(task.id);
    const projectKey = projectKeys.get(task.projectId) ?? "";
    return {
      id: task.id,
      taskId: formatTaskKey(projectKey, task.taskNumber),
      projectId: task.projectId,
      projectKey,
      taskNumber: task.taskNumber,
      title: task.title,
      status: task.status,
      column: card?.column,
      cardId: card?.id,
      dependsOn: normalizeDependencyIds(task.dependsOn),
    };
  });
  return workOrderFromNodes(nodes);
};

export const workOrderFromNodes = (nodes: TaskGraphNode[]): TaskWorkOrder => {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const edges = new Map(
    nodes.map((node) => [
      node.id,
      node.dependsOn.filter((id) => nodeById.has(id)),
    ]),
  );
  const { ordered, cyclicTaskIds } = orderNodes(edges);
  const waves = new Map<string, number>();
  for (const id of ordered) {
    const dependencyWaves = (edges.get(id) ?? []).map(
      (dependencyId) => (waves.get(dependencyId) ?? 0) + 1,
    );
    waves.set(id, Math.max(0, ...dependencyWaves));
  }

  const completeById = new Map(
    nodes.map((node) => [node.id, isTaskComplete(node)]),
  );
  const items = ordered
    .map((id) => nodeById.get(id) as TaskGraphNode)
    .sort(
      (a, b) =>
        (waves.get(a.id) ?? 0) - (waves.get(b.id) ?? 0) ||
        a.projectKey.localeCompare(b.projectKey) ||
        a.taskNumber - b.taskNumber,
    )
    .map((node, index): WorkOrderItem => {
      const blockedBy = (edges.get(node.id) ?? []).filter(
        (dependencyId) => !completeById.get(dependencyId),
      );
      const isComplete = completeById.get(node.id) ?? false;
      return {
        id: node.id,
        taskId: node.taskId,
        projectId: node.projectId,
        title: node.title,
        status: node.status,
        ...(node.column ? { column: node.column } : {}),
        ...(node.cardId ? { cardId: node.cardId } : {}),
        position: index + 1,
        wave: waves.get(node.id) ?? 0,
        dependsOn: node.dependsOn,
        blockedBy,
        isBlocked: blockedBy.length > 0,
        isComplete,
        isActionable: !isComplete && blockedBy.length === 0,
      };
    });

  return { items, cyclicTaskIds };
};

/** Kahn's algorithm: dependencies first, whatever is left over sits in a cycle. */
const orderNodes = (edges: Map<string, string[]>) => {
  const dependents = new Map<string, string[]>();
  const remaining = new Map<string, number>();
  for (const [id, dependencies] of edges) {
    const known = dependencies.filter((dependencyId) =>
      edges.has(dependencyId),
    );
    remaining.set(id, known.length);
    for (const dependencyId of known) {
      dependents.set(dependencyId, [
        ...(dependents.get(dependencyId) ?? []),
        id,
      ]);
    }
  }

  const ready = [...remaining]
    .filter(([, count]) => count === 0)
    .map(([id]) => id);
  const ordered: string[] = [];
  while (ready.length > 0) {
    const id = ready.shift() as string;
    ordered.push(id);
    for (const dependentId of dependents.get(id) ?? []) {
      const count = (remaining.get(dependentId) ?? 0) - 1;
      remaining.set(dependentId, count);
      if (count === 0) {
        ready.push(dependentId);
      }
    }
  }

  const orderedIds = new Set(ordered);
  return {
    ordered,
    cyclicTaskIds: [...edges.keys()].filter((id) => !orderedIds.has(id)),
  };
};
