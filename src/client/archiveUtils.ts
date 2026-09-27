import type { BoardCard, Idea } from "../shared/types.js";
import { formatTaskId } from "./taskIdentity.js";

export type ArchivedTaskRow = {
  cardId: string;
  ideaId?: string;
  /** Empty when the card has no task left. */
  taskId: string;
  taskNumber?: number;
  title: string;
  labels: string[];
  branchName?: string;
  archivedAt: string;
};

export type ArchiveSortOption =
  | "archived_desc"
  | "archived_asc"
  | "task_asc"
  | "title_asc";

export const archiveSortOptions: { value: ArchiveSortOption; label: string }[] =
  [
    { value: "archived_desc", label: "Recently archived" },
    { value: "archived_asc", label: "Oldest archived" },
    { value: "task_asc", label: "Task ID" },
    { value: "title_asc", label: "Title A-Z" },
  ];

/** One row per archived card, named after its task when the task still exists. */
export const archivedTaskRows = (
  cards: BoardCard[],
  ideaById: Map<string, Idea>,
  projectKey: string,
): ArchivedTaskRow[] =>
  cards.map((card) => {
    const idea = card.ideaId ? ideaById.get(card.ideaId) : undefined;
    return {
      cardId: card.id,
      ideaId: card.ideaId,
      taskId: idea ? formatTaskId(projectKey, idea.taskNumber) : "",
      taskNumber: idea?.taskNumber,
      title: idea?.title ?? card.title,
      labels: [...(idea?.labels ?? card.labels)],
      branchName: card.branchName,
      archivedAt: card.archivedAt ?? card.updatedAt,
    };
  });

const byTitle = (a: ArchivedTaskRow, b: ArchivedTaskRow) =>
  a.title.localeCompare(b.title);

const comparators: Record<
  ArchiveSortOption,
  (a: ArchivedTaskRow, b: ArchivedTaskRow) => number
> = {
  archived_desc: (a, b) =>
    b.archivedAt.localeCompare(a.archivedAt) || byTitle(a, b),
  archived_asc: (a, b) =>
    a.archivedAt.localeCompare(b.archivedAt) || byTitle(a, b),
  // Cards without a task sort last.
  task_asc: (a, b) =>
    (a.taskNumber ?? Number.POSITIVE_INFINITY) -
      (b.taskNumber ?? Number.POSITIVE_INFINITY) || byTitle(a, b),
  title_asc: byTitle,
};

export const filterAndSortArchivedTasks = (
  rows: ArchivedTaskRow[],
  { search, sort }: { search: string; sort: ArchiveSortOption },
): ArchivedTaskRow[] => {
  const term = search.trim().toLowerCase();
  const matches = term
    ? rows.filter((row) =>
        [row.taskId, row.title, row.branchName ?? "", ...row.labels].some(
          (value) => value.toLowerCase().includes(term),
        ),
      )
    : rows;
  return [...matches].sort(comparators[sort]);
};
