import { type BoardColumn, boardColumns } from "../shared/types.js";

/** The phone breakpoint of styles.css; below it the board shows one column at a time. */
export const phoneBoardQuery = "(max-width: 620px)";

export const boardColumnStorageKey = (projectId: string) =>
  `vibepod-board.board-column.${projectId}`;

const isBoardColumn = (value: unknown): value is BoardColumn =>
  boardColumns.includes(value as BoardColumn);

// Storage can be unavailable (privacy modes, sandboxed frames), so reading and writing
// the choice never throws; the board then opens its default column.
export const readBoardColumn = (projectId: string): BoardColumn | null => {
  try {
    const stored = window.localStorage.getItem(
      boardColumnStorageKey(projectId),
    );
    return isBoardColumn(stored) ? stored : null;
  } catch {
    return null;
  }
};

export const writeBoardColumn = (projectId: string, column: BoardColumn) => {
  try {
    window.localStorage.setItem(boardColumnStorageKey(projectId), column);
  } catch {
    // Keep the in-memory choice for this session.
  }
};

const preferredColumns: BoardColumn[] = [
  "in_progress",
  "review",
  ...boardColumns.filter(
    (column) => column !== "in_progress" && column !== "review",
  ),
];

/**
 * The column a phone opens before anything was picked: the first that has cards,
 * preferring the work in progress and then what waits for review.
 */
export const defaultBoardColumn = (
  columns: Partial<Record<BoardColumn, readonly unknown[]>>,
): BoardColumn =>
  preferredColumns.find((column) => (columns[column]?.length ?? 0) > 0) ??
  boardColumns[0];
