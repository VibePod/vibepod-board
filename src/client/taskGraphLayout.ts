import {
  isTaskComplete,
  type TaskGraphNode,
  workOrderFromNodes,
} from "../shared/dependencies.js";
import type { BoardColumn, Idea, IdeaStatus } from "../shared/types.js";
import { formatTaskId } from "./taskIdentity.js";

/** Node box geometry; the CSS node styles assume these exact dimensions. */
export const NODE_WIDTH = 220;
export const NODE_HEIGHT = 84;
/**
 * Lane and row gaps are wide on purpose: dense dependency graphs are unreadable
 * when the curves have no room to separate, and the extra space costs nothing
 * but scrolling.
 */
export const COLUMN_GAP = 150;
export const ROW_GAP = 36;
export const PADDING = 16;
export const LANE_LABEL_HEIGHT = 26;
/** Minimum horizontal pull on an edge curve, so backward edges still bend visibly. */
const MIN_BEND = 32;
/**
 * Edges touching the same node fan out over its side instead of stacking on the
 * midpoint. The inset keeps the outermost anchors clear of the rounded corners.
 */
const ANCHOR_INSET = 12;

export type TaskGraphLayoutNode = {
  id: string;
  taskId: string;
  title: string;
  status: IdeaStatus;
  column?: BoardColumn;
  /** Dependency depth: 0 has no blockers inside the graph. */
  wave: number;
  row: number;
  x: number;
  y: number;
  isBlocked: boolean;
  isComplete: boolean;
  /** True when the task could not be ordered because it sits in or behind a cycle. */
  isCyclic: boolean;
  /** Blockers drawn in this graph. */
  dependsOn: string[];
  /** Blockers left out by the current filters, reported as a hint on the node. */
  hiddenDependencyCount: number;
};

export type TaskGraphLayoutEdge = {
  id: string;
  /** The blocker. */
  fromId: string;
  /** The task waiting for it. */
  toId: string;
  /** True while the blocker is neither done nor denied, so the edge still holds work back. */
  isBlocking: boolean;
  /** Cubic bezier from the blocker's right edge to the dependent's left edge. */
  path: string;
};

export type TaskGraphLane = {
  wave: number;
  label: string;
  x: number;
  width: number;
  nodeCount: number;
  isCycle: boolean;
};

export type TaskGraphLayout = {
  nodes: TaskGraphLayoutNode[];
  edges: TaskGraphLayoutEdge[];
  lanes: TaskGraphLane[];
  width: number;
  height: number;
  nodeWidth: number;
  nodeHeight: number;
  hasCycle: boolean;
};

export type TaskGraphLayoutOptions = {
  projectKey: string;
  /** Board column per task id, used to tell finished blockers from pending ones. */
  cardColumns?: Map<string, BoardColumn>;
};

/**
 * Lays the dependency graph out as left-to-right waves: blockers sit left of the
 * tasks waiting for them. Only the tasks handed in are placed, so the graph
 * follows the task filters; dependencies pointing outside that set are counted
 * on the node instead of drawn. Tasks in a cycle cannot be ordered and get their
 * own trailing lane.
 */
export const buildTaskGraphLayout = (
  tasks: Idea[],
  { projectKey, cardColumns }: TaskGraphLayoutOptions,
): TaskGraphLayout => {
  const visibleIds = new Set(tasks.map((task) => task.id));
  const graphNodes: TaskGraphNode[] = tasks.map((task) => {
    const column = cardColumns?.get(task.id);
    return {
      id: task.id,
      taskId: formatTaskId(projectKey, task.taskNumber),
      projectId: task.projectId,
      projectKey,
      taskNumber: task.taskNumber,
      title: task.title,
      status: task.status,
      ...(column ? { column } : {}),
      dependsOn: task.dependsOn,
    };
  });

  const { items, cyclicTaskIds } = workOrderFromNodes(graphNodes);
  const waveByTaskId = new Map(items.map((item) => [item.id, item.wave]));
  const cyclic = new Set(cyclicTaskIds);
  const cycleWave =
    items.length > 0 ? Math.max(...items.map((item) => item.wave)) + 1 : 0;

  const placed: TaskGraphLayoutNode[] = [];
  const rowById = new Map<string, number>();
  const lanes: TaskGraphLane[] = [];

  const laneWaves = [
    ...new Set(
      tasks.map((task) =>
        cyclic.has(task.id) ? cycleWave : (waveByTaskId.get(task.id) ?? 0),
      ),
    ),
  ].sort((a, b) => a - b);

  for (const wave of laneWaves) {
    const laneTasks = tasks
      .filter(
        (task) =>
          (cyclic.has(task.id) ? cycleWave : waveByTaskId.get(task.id)) ===
          wave,
      )
      .sort(
        (a, b) =>
          barycenter(a, rowById) - barycenter(b, rowById) ||
          a.taskNumber - b.taskNumber,
      );

    laneTasks.forEach((task, row) => {
      rowById.set(task.id, row);
      const dependsOn = task.dependsOn.filter((id) => visibleIds.has(id));
      const column = cardColumns?.get(task.id);
      placed.push({
        id: task.id,
        taskId: formatTaskId(projectKey, task.taskNumber),
        title: task.title,
        status: task.status,
        ...(column ? { column } : {}),
        wave,
        row,
        x: PADDING + wave * (NODE_WIDTH + COLUMN_GAP),
        y: PADDING + LANE_LABEL_HEIGHT + row * (NODE_HEIGHT + ROW_GAP),
        isBlocked: task.blockedBy.length > 0,
        isComplete: isTaskComplete({
          status: task.status,
          ...(column ? { column } : {}),
        }),
        isCyclic: cyclic.has(task.id),
        dependsOn,
        hiddenDependencyCount: task.dependsOn.length - dependsOn.length,
      });
    });

    lanes.push({
      wave,
      label: laneLabel(wave, cyclic.size > 0 && wave === cycleWave),
      x: PADDING + wave * (NODE_WIDTH + COLUMN_GAP),
      width: NODE_WIDTH,
      nodeCount: laneTasks.length,
      isCycle: cyclic.size > 0 && wave === cycleWave,
    });
  }

  const nodeById = new Map(placed.map((node) => [node.id, node]));
  const pairs = placed.flatMap((node) =>
    node.dependsOn
      .map((blockerId) => nodeById.get(blockerId))
      .filter((blocker): blocker is TaskGraphLayoutNode => Boolean(blocker))
      .map((blocker) => ({ blocker, dependent: node })),
  );

  // Leaving and arriving edges are ordered by the node they reach, so fanned
  // anchors run top to bottom in the same order as their counterparts.
  const outgoing = groupBy(
    pairs,
    (pair) => pair.blocker.id,
    (a, b) => a.dependent.y - b.dependent.y || a.dependent.x - b.dependent.x,
  );
  const incoming = groupBy(
    pairs,
    (pair) => pair.dependent.id,
    (a, b) => a.blocker.y - b.blocker.y || a.blocker.x - b.blocker.x,
  );

  const edges = pairs.map(({ blocker, dependent }): TaskGraphLayoutEdge => {
    const fromGroup = outgoing.get(blocker.id) ?? [];
    const toGroup = incoming.get(dependent.id) ?? [];
    const startY = anchorY(
      blocker,
      fromGroup.findIndex((pair) => pair.dependent.id === dependent.id),
      fromGroup.length,
    );
    const endY = anchorY(
      dependent,
      toGroup.findIndex((pair) => pair.blocker.id === blocker.id),
      toGroup.length,
    );
    return {
      id: `${blocker.id}->${dependent.id}`,
      fromId: blocker.id,
      toId: dependent.id,
      isBlocking: !blocker.isComplete,
      path: edgePath(blocker, dependent, startY, endY),
    };
  });

  const rowCount = Math.max(0, ...lanes.map((lane) => lane.nodeCount));
  return {
    nodes: placed,
    edges,
    lanes,
    width:
      lanes.length > 0
        ? Math.max(...lanes.map((lane) => lane.x + lane.width)) + PADDING
        : 0,
    height:
      rowCount > 0
        ? PADDING * 2 +
          LANE_LABEL_HEIGHT +
          rowCount * NODE_HEIGHT +
          (rowCount - 1) * ROW_GAP
        : 0,
    nodeWidth: NODE_WIDTH,
    nodeHeight: NODE_HEIGHT,
    hasCycle: cyclic.size > 0,
  };
};

/**
 * Average row of the blockers already placed to the left, so a task lands next
 * to what it waits for. Tasks without a placed blocker sink to the lane bottom.
 */
const barycenter = (idea: Idea, rowById: Map<string, number>): number => {
  const rows = idea.dependsOn
    .map((id) => rowById.get(id))
    .filter((row): row is number => row !== undefined);
  if (rows.length === 0) {
    return Number.MAX_SAFE_INTEGER;
  }
  return rows.reduce((total, row) => total + row, 0) / rows.length;
};

const groupBy = <T>(
  items: T[],
  key: (item: T) => string,
  compare: (a: T, b: T) => number,
): Map<string, T[]> => {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    groups.set(key(item), [...(groups.get(key(item)) ?? []), item]);
  }
  for (const group of groups.values()) {
    group.sort(compare);
  }
  return groups;
};

/**
 * Vertical anchor for edge `index` of `count` edges on one node side: a single
 * edge keeps the midpoint, more spread evenly over the node's inset height.
 */
const anchorY = (
  node: Pick<TaskGraphLayoutNode, "y">,
  index: number,
  count: number,
): number => {
  if (count <= 1 || index < 0) {
    return node.y + NODE_HEIGHT / 2;
  }
  const band = NODE_HEIGHT - ANCHOR_INSET * 2;
  return Math.round(node.y + ANCHOR_INSET + (band * (index + 1)) / (count + 1));
};

const edgePath = (
  from: Pick<TaskGraphLayoutNode, "x" | "y">,
  to: Pick<TaskGraphLayoutNode, "x" | "y">,
  startY: number,
  endY: number,
): string => {
  const startX = from.x + NODE_WIDTH;
  const endX = to.x;
  const bend = Math.round(Math.max(MIN_BEND, (endX - startX) / 2));
  return `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`;
};

const laneLabel = (wave: number, isCycle: boolean): string => {
  if (isCycle) {
    return "Cycle";
  }
  return wave === 0 ? "Wave 0 · unblocked" : `Wave ${wave}`;
};
