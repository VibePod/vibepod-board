import type { ApiTokenProject } from "../shared/types.js";

export const formatTokenProjectLabel = (project: ApiTokenProject) =>
  project.title.trim()
    ? `${project.key} · ${project.title.trim()}`
    : project.key;
