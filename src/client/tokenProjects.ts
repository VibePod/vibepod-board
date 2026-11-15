import type { ApiTokenProject } from "../shared/types.js";

export const formatTokenProjectLabel = (
  project: Pick<ApiTokenProject, "key" | "title">,
) =>
  project.title.trim()
    ? `${project.key} · ${project.title.trim()}`
    : project.key;
