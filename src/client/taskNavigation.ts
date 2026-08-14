import type { Idea } from "../shared/types.js";

export type TaskNavigation = {
  index: number;
  total: number;
  previous: Idea;
  next: Idea;
};

/**
 * Describes where the given task sits inside its project so the task modal can
 * offer left/right navigation. Wraps around at both ends and returns null when
 * the task is not part of the list (for example while creating a new task).
 */
export const taskNavigationFor = (
  ideas: Idea[],
  currentId: string | undefined,
): TaskNavigation | null => {
  if (!currentId) {
    return null;
  }
  const index = ideas.findIndex((idea) => idea.id === currentId);
  if (index === -1) {
    return null;
  }
  const total = ideas.length;
  return {
    index,
    total,
    previous: ideas[(index - 1 + total) % total],
    next: ideas[(index + 1) % total],
  };
};
