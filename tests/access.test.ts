import { describe, expect, it } from "vitest";

import {
  adminAccess,
  assertCanAccessProject,
  defaultProjectIdForCreate,
  filterProjectIds,
  tokenAccess
} from "../src/server/store.js";

describe("project access helpers", () => {
  it("lets admins access every project", () => {
    const access = adminAccess("admin");

    expect(filterProjectIds(access, ["one", "two"])).toEqual(["one", "two"]);
    expect(() => assertCanAccessProject(access, "outside")).not.toThrow();
  });

  it("limits token access to mapped projects", () => {
    const access = tokenAccess("token-1", ["project-1", "project-2"]);

    expect(filterProjectIds(access, ["project-1", "project-3"])).toEqual(["project-1"]);
    expect(defaultProjectIdForCreate(access, undefined)).toBe("project-1");
    expect(defaultProjectIdForCreate(access, "project-2")).toBe("project-2");
    expect(() => defaultProjectIdForCreate(access, "project-3")).toThrow(
      "Token is not allowed to access project: project-3"
    );
    expect(() => assertCanAccessProject(access, "project-3")).toThrow(
      "Token is not allowed to access project: project-3"
    );
  });

  it("rejects create defaults when a token has no projects", () => {
    expect(() => defaultProjectIdForCreate(tokenAccess("token-1", []), undefined)).toThrow(
      "Token is not mapped to any projects"
    );
  });
});
