import { describe, expect, it } from "vitest";

import {
  fullAccess,
  hasFullAccess,
  permissionsFor,
  readOnly,
  summarizePermissions,
} from "../src/client/tokenPermissions.js";
import type { PermissionArea } from "../src/shared/types.js";

const area = (
  key: string,
  label: string,
  levels: PermissionArea["levels"] = ["none", "read", "write"],
): PermissionArea => ({
  key,
  label,
  description: "",
  levels,
  readTools: [],
  writeTools: [],
});

const areas = [
  area("tasks", "Tasks"),
  area("board", "Board"),
  area("project", "Project", ["none", "write"]),
];

describe("token permissions", () => {
  it("grants the highest level of every area as full access", () => {
    expect(fullAccess(areas)).toEqual({
      tasks: "write",
      board: "write",
      project: "write",
    });
  });

  it("leaves out areas that cannot be read from a read-only set", () => {
    expect(readOnly(areas)).toEqual({
      tasks: "read",
      board: "read",
      project: "none",
    });
  });

  it("recognises full access only when every area has its highest level", () => {
    expect(hasFullAccess(areas, fullAccess(areas))).toBe(true);
    expect(hasFullAccess(areas, { ...fullAccess(areas), board: "read" })).toBe(
      false,
    );
    expect(hasFullAccess([], {})).toBe(false);
  });

  it("fills areas a set does not name with no access", () => {
    expect(permissionsFor(areas, { tasks: "read" })).toEqual({
      tasks: "read",
      board: "none",
      project: "none",
    });
  });

  it("starts from full access when no set was chosen yet", () => {
    expect(permissionsFor(areas, undefined)).toEqual(fullAccess(areas));
  });

  it("summarises a set as the areas it reaches", () => {
    expect(summarizePermissions(areas, fullAccess(areas))).toEqual([
      "Full access",
    ]);
    expect(
      summarizePermissions(areas, {
        tasks: "read",
        board: "write",
        project: "none",
      }),
    ).toEqual(["Tasks · Read", "Board · Write"]);
    expect(summarizePermissions(areas, readOnly([]))).toEqual(["No access"]);
  });
});
