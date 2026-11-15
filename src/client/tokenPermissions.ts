import type {
  PermissionArea,
  PermissionLevel,
  TokenPermissions,
} from "../shared/types.js";

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
