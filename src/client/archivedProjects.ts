import type { BoardCard, BoardColumns, Project } from "../shared/types.js";
import { boardColumns } from "../shared/types.js";

/** Projects that are not archived: the ones lists, pickers and switchers offer. */
export const activeProjects = (projects: Project[]): Project[] =>
  projects.filter((project) => !project.archivedAt);

/** Archived projects, most recently archived first. */
export const sortArchivedProjects = (projects: Project[]): Project[] =>
  projects
    .filter((project) => !!project.archivedAt)
    .sort(
      (left, right) =>
        (right.archivedAt ?? "").localeCompare(left.archivedAt ?? "") ||
        left.key.localeCompare(right.key),
    );

/** A permanent delete is confirmed by typing the project key (any case). */
export const isProjectKeyConfirmed = (typed: string, projectKey: string) =>
  typed.trim().toUpperCase() === projectKey.toUpperCase();

export const formatProjectCounts = (project: Project): string => {
  const tasks = project.taskCount ?? 0;
  const cards = project.cardCount ?? 0;
  return `${tasks} ${tasks === 1 ? "task" : "tasks"}, ${cards} ${cards === 1 ? "card" : "cards"}`;
};

/** What a permanent delete removes, listed before the user confirms it. */
export const projectDeletionConsequences = (project: Project): string[] => [
  `Removes ${formatProjectCounts(project)}, and all notes of the project.`,
  "Removes its workers and its links to API tokens; the tokens stay.",
  "This cannot be undone. Export the project first to keep a copy.",
];

/** The board's non-empty columns in board order, for a read-only overview. */
export const nonEmptyColumns = (
  columns: BoardColumns,
): { column: (typeof boardColumns)[number]; cards: BoardCard[] }[] =>
  boardColumns
    .map((column) => ({ column, cards: columns[column] ?? [] }))
    .filter(({ cards }) => cards.length > 0);
