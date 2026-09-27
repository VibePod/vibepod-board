import {
  type BoardColumns,
  boardColumns,
  type Idea,
  type IdeaStatus,
} from "../shared/types.js";

export const taskSortOptions = [
  "created_desc",
  "updated_desc",
  "title_asc",
  "status_asc",
  "rating_desc",
  "dependency_asc",
] as const;

export type TaskSortOption = (typeof taskSortOptions)[number];

export type TaskListFilters = {
  sort?: TaskSortOption;
  status?: IdeaStatus | "";
  label?: string;
  search?: string;
  /** Project key used to match full task ID queries such as "VP-85". */
  projectKey?: string;
  /** Task id to work-order position, used by the dependency_asc sort. */
  workOrder?: Map<string, number>;
};

export const filterAndSortTasks = (
  tasks: Idea[],
  filters: TaskListFilters,
): Idea[] => {
  const label = filters.label?.trim() ?? "";
  const status = filters.status ?? "";
  const sort = filters.sort ?? "created_desc";

  return tasks
    .filter((task) => {
      if (status && task.status !== status) {
        return false;
      }
      if (label && !task.labels.includes(label)) {
        return false;
      }
      return matchesTaskSearch(
        { ...task, projectKey: filters.projectKey },
        filters.search,
      );
    })
    .sort((a, b) => compareTasks(a, b, sort, filters.workOrder));
};

/** What a search query can see of a task or board card. */
export type TaskSearchTarget = {
  projectKey?: string;
  /** Absent for board cards that are not linked to a task. */
  taskNumber?: number;
  title: string;
  summary?: string;
  details?: string;
  labels: string[];
};

const taskIdQuery = /^([a-z]+)-(\d+)$/;
const taskNumberQuery = /^#?(\d+)$/;

/**
 * Shared search for the task list and the board. A bare number ("85", "#85") or a full
 * task ID of the target's project ("VP-85", any case) matches that task number exactly;
 * anything else is a case-insensitive substring search over the text fields.
 */
export const matchesTaskSearch = (
  target: TaskSearchTarget,
  search: string | undefined,
): boolean => {
  const query = search?.trim().toLocaleLowerCase() ?? "";
  if (!query) {
    return true;
  }
  const numberMatch = taskNumberQuery.exec(query);
  if (numberMatch) {
    return target.taskNumber === Number(numberMatch[1]);
  }
  const idMatch = taskIdQuery.exec(query);
  if (idMatch && idMatch[1] === target.projectKey?.toLocaleLowerCase()) {
    return target.taskNumber === Number(idMatch[2]);
  }
  return [target.title, target.summary, target.details, ...target.labels]
    .join(" ")
    .toLocaleLowerCase()
    .includes(query);
};

/**
 * Filters every board column with the task search. Cards linked to a task match on the
 * task; cards without one match on their own title, details and labels.
 */
export const filterColumnsBySearch = (
  columns: BoardColumns,
  search: string,
  projectKey: string,
  ideaById: Map<string, Idea>,
): BoardColumns => {
  const filtered = { ...columns };
  for (const column of boardColumns) {
    filtered[column] = (columns[column] ?? []).filter((card) => {
      const idea = card.ideaId ? ideaById.get(card.ideaId) : undefined;
      return matchesTaskSearch({ ...(idea ?? card), projectKey }, search);
    });
  }
  return filtered;
};

const compareTasks = (
  a: Idea,
  b: Idea,
  sort: TaskSortOption,
  workOrder?: Map<string, number>,
) => {
  if (sort === "dependency_asc") {
    // Tasks missing from the work order sit in a dependency cycle; park them last.
    const aPosition = workOrder?.get(a.id) ?? Number.MAX_SAFE_INTEGER;
    const bPosition = workOrder?.get(b.id) ?? Number.MAX_SAFE_INTEGER;
    return aPosition - bPosition || a.taskNumber - b.taskNumber;
  }
  if (sort === "updated_desc") {
    return (
      b.updatedAt.localeCompare(a.updatedAt) ||
      b.createdAt.localeCompare(a.createdAt)
    );
  }
  if (sort === "title_asc") {
    return (
      a.title.localeCompare(b.title) || b.createdAt.localeCompare(a.createdAt)
    );
  }
  if (sort === "status_asc") {
    return (
      a.status.localeCompare(b.status) || b.createdAt.localeCompare(a.createdAt)
    );
  }
  if (sort === "rating_desc") {
    // Highest rating first; unrated tasks sink to the bottom; ties fall back to newest created.
    const aScore = a.readinessScore ?? -1;
    const bScore = b.readinessScore ?? -1;
    return bScore - aScore || b.createdAt.localeCompare(a.createdAt);
  }
  return (
    b.createdAt.localeCompare(a.createdAt) ||
    b.updatedAt.localeCompare(a.updatedAt)
  );
};
