import { Alert, Badge, Box, Group, Paper, Stack, Text } from "@mantine/core";
import { Lock } from "lucide-react";
import { useEffect, useState } from "react";
import type { BoardColumn, Idea } from "../shared/types.js";
import {
  buildTaskGraphLayout,
  type TaskGraphLayoutEdge,
  type TaskGraphLayoutNode,
} from "./taskGraphLayout.js";

type TaskGraphProps = {
  tasks: Idea[];
  projectKey: string;
  cardColumns: Map<string, BoardColumn>;
  onOpenTask: (task: Idea) => void;
};

/**
 * Dependency graph of the visible tasks: blockers on the left, the tasks waiting
 * for them on the right. Edges are drawn on an SVG layer underneath absolutely
 * positioned task buttons, so the nodes stay real focusable buttons.
 *
 * Picking an edge focuses the two tasks it connects and fades the rest, which is
 * the only way to follow a single dependency once a project has many of them.
 */
export const TaskGraph = ({
  tasks,
  projectKey,
  cardColumns,
  onOpenTask,
}: TaskGraphProps) => {
  const layout = buildTaskGraphLayout(tasks, { projectKey, cardColumns });
  const taskById = new Map(tasks.map((task) => [task.id, task]));
  const nodeById = new Map(layout.nodes.map((node) => [node.id, node]));
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const selectedEdge =
    layout.edges.find((edge) => edge.id === selectedEdgeId) ?? null;

  // Any click that misses an edge, and Escape, drop the focus again.
  useEffect(() => {
    if (!selectedEdge) {
      return;
    }
    const clearOnOutsideClick = (event: MouseEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest?.(".task-graph-edge-group")) {
        setSelectedEdgeId(null);
      }
    };
    const clearOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelectedEdgeId(null);
      }
    };
    window.addEventListener("click", clearOnOutsideClick);
    window.addEventListener("keydown", clearOnEscape);
    return () => {
      window.removeEventListener("click", clearOnOutsideClick);
      window.removeEventListener("keydown", clearOnEscape);
    };
  }, [selectedEdge]);

  const isFaded = (id: string) =>
    Boolean(selectedEdge) &&
    selectedEdge?.fromId !== id &&
    selectedEdge?.toId !== id;
  const toggleEdge = (edgeId: string) =>
    setSelectedEdgeId((current) => (current === edgeId ? null : edgeId));

  return (
    <Paper className="task-graph" withBorder radius="md" p="md">
      <Stack gap="sm">
        <Group justify="space-between" gap="sm">
          <Text size="sm" c="dimmed">
            Blockers point to the tasks waiting for them. Click an arrow to
            focus the two tasks it connects.
          </Text>
          <Group className="task-graph-legend" gap="md">
            <Group gap={6}>
              <span className="task-graph-legend-line blocking" aria-hidden />
              <Text size="xs" c="dimmed">
                Still blocking
              </Text>
            </Group>
            <Group gap={6}>
              <span className="task-graph-legend-line satisfied" aria-hidden />
              <Text size="xs" c="dimmed">
                Satisfied
              </Text>
            </Group>
          </Group>
        </Group>
        {layout.hasCycle && (
          <Alert color="orange" variant="light">
            Some tasks depend on each other in a cycle and cannot be ordered.
            They are grouped in the <strong>Cycle</strong> lane.
          </Alert>
        )}
        {selectedEdge && (
          <Text className="task-graph-selection" size="sm">
            {edgeSummary(selectedEdge, nodeById)}{" "}
            <Text component="span" size="sm" c="dimmed">
              Press Escape or click anywhere else to show all tasks again.
            </Text>
          </Text>
        )}
        <Box className="task-graph-viewport">
          <Box
            className="task-graph-canvas"
            data-focused={selectedEdge ? "true" : undefined}
            style={{ width: layout.width, height: layout.height }}
          >
            <svg
              className="task-graph-edges"
              width={layout.width}
              height={layout.height}
            >
              <title>Task dependency edges</title>
              <defs>
                {["blocking", "satisfied", "selected"].map((kind) => (
                  <marker
                    id={`task-graph-arrow-${kind}`}
                    key={kind}
                    markerWidth="7"
                    markerHeight="7"
                    refX="6"
                    refY="3.5"
                    orient="auto"
                  >
                    <path className={kind} d="M 0 0 L 7 3.5 L 0 7 z" />
                  </marker>
                ))}
              </defs>
              {layout.edges.map((edge) => {
                const isSelected = edge.id === selectedEdgeId;
                const kind = isSelected
                  ? "selected"
                  : edge.isBlocking
                    ? "blocking"
                    : "satisfied";
                return (
                  // biome-ignore lint/a11y/useSemanticElements: an SVG edge cannot be a button element, so the ARIA button pattern on the group is the accessible equivalent
                  <g
                    className="task-graph-edge-group"
                    key={edge.id}
                    role="button"
                    tabIndex={0}
                    aria-pressed={isSelected}
                    aria-label={`${edgeSummary(edge, nodeById)} Focus these tasks.`}
                    data-selected={isSelected ? "true" : undefined}
                    data-faded={
                      selectedEdge && !isSelected ? "true" : undefined
                    }
                    onClick={() => toggleEdge(edge.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        toggleEdge(edge.id);
                      }
                    }}
                  >
                    <path className="task-graph-edge-hit" d={edge.path} />
                    <path
                      className={`task-graph-edge ${kind}`}
                      d={edge.path}
                      markerEnd={`url(#task-graph-arrow-${kind})`}
                    />
                  </g>
                );
              })}
            </svg>
            {layout.lanes.map((lane) => (
              <Box
                className={`task-graph-lane-label${lane.isCycle ? " cycle" : ""}`}
                key={lane.wave}
                style={{ left: lane.x, width: lane.width }}
              >
                {lane.label}
              </Box>
            ))}
            {layout.nodes.map((node) => {
              const task = taskById.get(node.id);
              const isFocused = Boolean(selectedEdge) && !isFaded(node.id);
              return (
                <button
                  type="button"
                  className="task-graph-node"
                  key={node.id}
                  data-status={node.status}
                  data-blocked={node.isBlocked ? "true" : undefined}
                  data-complete={node.isComplete ? "true" : undefined}
                  data-faded={isFaded(node.id) ? "true" : undefined}
                  data-focused={isFocused ? "true" : undefined}
                  aria-label={nodeLabel(node)}
                  style={{
                    left: node.x,
                    top: node.y,
                    width: layout.nodeWidth,
                    height: layout.nodeHeight,
                  }}
                  onClick={() => task && onOpenTask(task)}
                >
                  <span className="task-graph-node-head">
                    <Badge variant="light" color="gray" size="xs">
                      {node.taskId}
                    </Badge>
                    <Badge variant="light" size="xs" tt="capitalize">
                      {node.column
                        ? node.column.replace("_", " ")
                        : node.status}
                    </Badge>
                  </span>
                  <span className="task-graph-node-title">{node.title}</span>
                  <span className="task-graph-node-meta">
                    {node.isBlocked && (
                      <span className="task-graph-node-blocked">
                        <Lock size={11} aria-hidden /> blocked
                      </span>
                    )}
                    {node.hiddenDependencyCount > 0 && (
                      <span className="task-graph-node-hidden">
                        {node.hiddenDependencyCount} blocker
                        {node.hiddenDependencyCount === 1 ? "" : "s"} filtered
                        out
                      </span>
                    )}
                  </span>
                </button>
              );
            })}
          </Box>
        </Box>
      </Stack>
    </Paper>
  );
};

const nodeLabel = (node: TaskGraphLayoutNode): string => {
  const parts = [
    `${node.taskId} ${node.title}`,
    node.isCyclic ? "in a dependency cycle" : `wave ${node.wave}`,
  ];
  if (node.isBlocked) {
    parts.push("blocked");
  }
  return `${parts.join(", ")}. Open task.`;
};

const edgeSummary = (
  edge: TaskGraphLayoutEdge,
  nodeById: Map<string, TaskGraphLayoutNode>,
): string => {
  const from = nodeById.get(edge.fromId);
  const to = nodeById.get(edge.toId);
  const state = edge.isBlocking ? "still blocking" : "already satisfied";
  return `${from?.taskId} ${from?.title} blocks ${to?.taskId} ${to?.title}, ${state}.`;
};
