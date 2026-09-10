import { type BoardColumns, boardColumns } from "../shared/types.js";

/**
 * Sentinel for "held by nobody". The empty string already means "no filter",
 * so unclaimed work needs a value of its own.
 */
export const unassignedFilterValue = "__unassigned__";

export type AssigneeOption = { value: string; label: string };

type Holdable = { assignee?: string };

/**
 * The holders present in the current project, so the filter only ever offers
 * names that select something. "Unassigned" appears only when there is free
 * work to find.
 */
export const assigneeFilterOptions = (
  ...groups: Holdable[][]
): AssigneeOption[] => {
  const holders = new Set<string>();
  let hasFree = false;
  for (const group of groups) {
    for (const item of group) {
      if (item.assignee) {
        holders.add(item.assignee);
      } else {
        hasFree = true;
      }
    }
  }
  const options: AssigneeOption[] = [{ value: "", label: "All assignees" }];
  if (hasFree) {
    options.push({ value: unassignedFilterValue, label: "Unassigned" });
  }
  for (const holder of Array.from(holders).sort((a, b) => a.localeCompare(b))) {
    options.push({ value: holder, label: holder });
  }
  return options;
};

export const matchesAssigneeFilter = (
  assignee: string | undefined,
  filter: string,
): boolean => {
  if (!filter) {
    return true;
  }
  if (filter === unassignedFilterValue) {
    return !assignee;
  }
  return assignee === filter;
};

/**
 * Keeps a holder selectable while their last card is being filtered on: the
 * options come from the unfiltered set, but a holder can still disappear when
 * someone else releases the task, and a filter pointing at nobody would hide
 * the whole board with no way back.
 */
export const resolveAssigneeFilter = (
  filter: string,
  options: AssigneeOption[],
): string => (options.some((option) => option.value === filter) ? filter : "");

export const filterColumnsByAssignee = (
  columns: BoardColumns,
  filter: string,
): BoardColumns => {
  const filtered: BoardColumns = {
    ready: [],
    planned: [],
    in_progress: [],
    review: [],
    done: [],
  };
  for (const column of boardColumns) {
    filtered[column] = (columns[column] ?? []).filter((card) =>
      matchesAssigneeFilter(card.assignee, filter),
    );
  }
  return filtered;
};
