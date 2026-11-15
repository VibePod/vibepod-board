import type {
  ApiTokenProject,
  CreateApiTokenInput,
  PermissionArea,
  PermissionLevel,
  TokenPermissions,
} from "../shared/types.js";
import { formatTokenProjectLabel } from "./tokenProjects.js";

export const permissionLevelLabels: Record<PermissionLevel, string> = {
  none: "None",
  read: "Read",
  write: "Write",
};

const highestLevel = (area: PermissionArea): PermissionLevel =>
  area.levels[area.levels.length - 1] ?? "none";

/** The highest level of every area: what a token gets unless permissions are chosen. */
export const fullAccess = (areas: PermissionArea[]): TokenPermissions =>
  Object.fromEntries(areas.map((area) => [area.key, highestLevel(area)]));

/** Read where an area can be read, nothing elsewhere. */
export const readOnly = (areas: PermissionArea[]): TokenPermissions =>
  Object.fromEntries(
    areas.map((area) => [
      area.key,
      area.levels.includes("read") ? "read" : "none",
    ]),
  );

/** A level for every area: the chosen set, or full access while none was chosen. */
export const permissionsFor = (
  areas: PermissionArea[],
  permissions: TokenPermissions | undefined,
): TokenPermissions =>
  permissions
    ? Object.fromEntries(
        areas.map((area) => [area.key, permissions[area.key] ?? "none"]),
      )
    : fullAccess(areas);

export const hasFullAccess = (
  areas: PermissionArea[],
  permissions: TokenPermissions,
): boolean =>
  areas.length > 0 &&
  areas.every((area) => permissions[area.key] === highestLevel(area));

/** Short labels for a token's permissions, such as "Tasks · Read". */
export const summarizePermissions = (
  areas: PermissionArea[],
  permissions: TokenPermissions,
): string[] => {
  if (hasFullAccess(areas, permissions)) {
    return ["Full access"];
  }
  const reached = areas
    .map((area) => ({ area, level: permissions[area.key] ?? "none" }))
    .filter(({ level }) => level !== "none")
    .map(
      ({ area, level }) => `${area.label} · ${permissionLevelLabels[level]}`,
    );
  return reached.length > 0 ? reached : ["No access"];
};

/**
 * The projects of a token and their permissions while a form edits them. The projects
 * share one set until `individual` gives each its own, which is offered from two
 * projects on.
 */
export type TokenAccessDraft = {
  projectIds: string[];
  individual: boolean;
  /** The set every project has while they share one; full access until one is chosen. */
  shared?: TokenPermissions;
  /** The set of each project while they are set individually. */
  byProject: Record<string, TokenPermissions>;
};

export const emptyAccessDraft = (): TokenAccessDraft => ({
  projectIds: [],
  individual: false,
  byProject: {},
});

const samePermissions = (
  areas: PermissionArea[],
  left: TokenPermissions,
  right: TokenPermissions,
): boolean =>
  areas.every(
    (area) => (left[area.key] ?? "none") === (right[area.key] ?? "none"),
  );

/** The set a project has in the draft. A project without one starts from full access. */
export const projectPermissionsFor = (
  areas: PermissionArea[],
  draft: TokenAccessDraft,
  projectId: string,
): TokenPermissions =>
  permissionsFor(
    areas,
    draft.individual ? draft.byProject[projectId] : draft.shared,
  );

/** Switches between one shared set and a set per project, keeping what is shown. */
export const withIndividual = (
  areas: PermissionArea[],
  draft: TokenAccessDraft,
  individual: boolean,
): TokenAccessDraft => {
  if (individual === draft.individual) {
    return draft;
  }
  if (individual) {
    const shared = permissionsFor(areas, draft.shared);
    return {
      ...draft,
      individual,
      byProject: Object.fromEntries(
        draft.projectIds.map((projectId) => [projectId, shared]),
      ),
    };
  }
  // One table again: it continues with the set of the first project.
  const [first] = draft.projectIds;
  return {
    ...draft,
    individual,
    shared: first ? projectPermissionsFor(areas, draft, first) : draft.shared,
  };
};

/** Changes the projects. With fewer than two there is one set again. */
export const withProjects = (
  areas: PermissionArea[],
  draft: TokenAccessDraft,
  projectIds: string[],
): TokenAccessDraft => {
  const next = { ...draft, projectIds };
  return projectIds.length < 2 ? withIndividual(areas, next, false) : next;
};

/** The draft for a token that exists: individual sets when its projects differ. */
export const accessDraftFor = (
  areas: PermissionArea[],
  projects: ApiTokenProject[],
): TokenAccessDraft => {
  const sets = projects.map((project) =>
    permissionsFor(areas, project.permissions),
  );
  const individual = sets.some((set) => !samePermissions(areas, set, sets[0]));
  return {
    projectIds: projects.map((project) => project.id),
    individual,
    shared: individual ? undefined : sets[0],
    byProject: Object.fromEntries(
      projects.map((project, index) => [project.id, sets[index]]),
    ),
  };
};

/** What the draft sends to the board: one set for all, or one per project. */
export const accessPayload = (
  areas: PermissionArea[],
  draft: TokenAccessDraft,
): Pick<
  CreateApiTokenInput,
  "projectIds" | "permissions" | "projectPermissions"
> => {
  if (areas.length === 0) {
    // Nothing could be chosen, and the board then grants full access.
    return { projectIds: draft.projectIds };
  }
  if (!draft.individual) {
    return {
      projectIds: draft.projectIds,
      permissions: permissionsFor(areas, draft.shared),
    };
  }
  return {
    projectIds: draft.projectIds,
    projectPermissions: Object.fromEntries(
      draft.projectIds.map((projectId) => [
        projectId,
        projectPermissionsFor(areas, draft, projectId),
      ]),
    ),
  };
};

/**
 * A token's permissions as rows of labels: one row when its projects agree, otherwise a
 * row per project, named after it.
 */
export const summarizeProjectPermissions = (
  areas: PermissionArea[],
  projects: ApiTokenProject[],
): { project?: string; badges: string[] }[] => {
  if (projects.length === 0) {
    return [];
  }
  const sets = projects.map((project) =>
    permissionsFor(areas, project.permissions),
  );
  if (sets.every((set) => samePermissions(areas, set, sets[0]))) {
    return [{ badges: summarizePermissions(areas, sets[0]) }];
  }
  return projects.map((project, index) => ({
    project: formatTokenProjectLabel(project),
    badges: summarizePermissions(areas, sets[index]),
  }));
};
