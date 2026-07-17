import type { Idea, IdeaStatus } from "../shared/types.js";

export const taskSortOptions = ["created_desc", "updated_desc", "title_asc", "status_asc", "rating_desc"] as const;

export type TaskSortOption = (typeof taskSortOptions)[number];

export type TaskListFilters = {
  sort?: TaskSortOption;
  status?: IdeaStatus | "";
  label?: string;
  search?: string;
};

export const filterAndSortTasks = (tasks: Idea[], filters: TaskListFilters): Idea[] => {
  const search = filters.search?.trim().toLocaleLowerCase() ?? "";
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
      if (!search) {
        return true;
      }
      return [task.title, task.summary, task.details, ...task.labels]
        .join(" ")
        .toLocaleLowerCase()
        .includes(search);
    })
    .sort((a, b) => compareTasks(a, b, sort));
};

const compareTasks = (a: Idea, b: Idea, sort: TaskSortOption) => {
  if (sort === "updated_desc") {
    return b.updatedAt.localeCompare(a.updatedAt) || b.createdAt.localeCompare(a.createdAt);
  }
  if (sort === "title_asc") {
    return a.title.localeCompare(b.title) || b.createdAt.localeCompare(a.createdAt);
  }
  if (sort === "status_asc") {
    return a.status.localeCompare(b.status) || b.createdAt.localeCompare(a.createdAt);
  }
  if (sort === "rating_desc") {
    // Highest rating first; unrated tasks sink to the bottom; ties fall back to newest created.
    const aScore = a.readinessScore ?? -1;
    const bScore = b.readinessScore ?? -1;
    return bScore - aScore || b.createdAt.localeCompare(a.createdAt);
  }
  return b.createdAt.localeCompare(a.createdAt) || b.updatedAt.localeCompare(a.updatedAt);
};
