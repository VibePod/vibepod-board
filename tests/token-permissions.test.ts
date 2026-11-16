import { describe, expect, it } from "vitest";

import {
  accessDraftFor,
  accessPayload,
  emptyAccessDraft,
  fullAccess,
  hasFullAccess,
  permissionsFor,
  projectPermissionsFor,
  readOnly,
  summarizePermissions,
  summarizeProjectPermissions,
  withIndividual,
  withProjects,
} from "../src/client/tokenPermissions.js";
import type {
  ApiTokenProject,
  PermissionArea,
  TokenPermissions,
} from "../src/shared/types.js";

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

  const reader: TokenPermissions = {
    tasks: "read",
    board: "none",
    project: "none",
  };
  const project = (
    id: string,
    key: string,
    permissions: TokenPermissions,
  ): ApiTokenProject => ({ id, key, title: key, permissions });

  describe("per project", () => {
    it("starts a new token with one shared set of full access", () => {
      const draft = { ...emptyAccessDraft(), projectIds: ["a", "b"] };

      expect(accessPayload(areas, draft)).toEqual({
        projectIds: ["a", "b"],
        permissions: fullAccess(areas),
      });
    });

    it("sends only the projects while the areas are unknown", () => {
      const draft = { ...emptyAccessDraft(), projectIds: ["a"] };

      expect(accessPayload([], draft)).toEqual({ projectIds: ["a"] });
    });

    it("gives every project the shared set when individual sets are switched on", () => {
      const shared = withProjects(
        areas,
        { ...emptyAccessDraft(), shared: reader },
        ["a", "b"],
      );

      const individual = withIndividual(areas, shared, true);

      expect(projectPermissionsFor(areas, individual, "a")).toEqual(reader);
      expect(projectPermissionsFor(areas, individual, "b")).toEqual(reader);
    });

    it("sends a set for each project once they are set individually", () => {
      const individual = withIndividual(
        areas,
        { ...emptyAccessDraft(), projectIds: ["a", "b"], shared: reader },
        true,
      );
      const draft = {
        ...individual,
        byProject: { ...individual.byProject, b: fullAccess(areas) },
      };

      expect(accessPayload(areas, draft)).toEqual({
        projectIds: ["a", "b"],
        projectPermissions: { a: reader, b: fullAccess(areas) },
      });
    });

    it("gives a project added later full access to start from", () => {
      const individual = withIndividual(
        areas,
        { ...emptyAccessDraft(), projectIds: ["a", "b"], shared: reader },
        true,
      );

      const added = withProjects(areas, individual, ["a", "b", "c"]);

      expect(projectPermissionsFor(areas, added, "a")).toEqual(reader);
      expect(projectPermissionsFor(areas, added, "c")).toEqual(
        fullAccess(areas),
      );
    });

    it("continues with the set of the project that is left", () => {
      const individual = withIndividual(
        areas,
        { ...emptyAccessDraft(), projectIds: ["a", "b"] },
        true,
      );
      const edited = {
        ...individual,
        byProject: { ...individual.byProject, b: reader },
      };

      const single = withProjects(areas, edited, ["b"]);

      expect(single.individual).toBe(false);
      expect(accessPayload(areas, single)).toEqual({
        projectIds: ["b"],
        permissions: reader,
      });
    });

    it("goes back to one set with the set of the first project", () => {
      const individual = withIndividual(
        areas,
        { ...emptyAccessDraft(), projectIds: ["a", "b"] },
        true,
      );
      const edited = {
        ...individual,
        byProject: { ...individual.byProject, a: reader },
      };

      const shared = withIndividual(areas, edited, false);

      expect(accessPayload(areas, shared)).toEqual({
        projectIds: ["a", "b"],
        permissions: reader,
      });
    });

    it("edits a token with one shared set when its projects agree", () => {
      const draft = accessDraftFor(areas, [
        project("a", "APP", reader),
        project("b", "API", reader),
      ]);

      expect(draft.individual).toBe(false);
      expect(accessPayload(areas, draft)).toEqual({
        projectIds: ["a", "b"],
        permissions: reader,
      });
    });

    it("edits a token project by project when its projects differ", () => {
      const draft = accessDraftFor(areas, [
        project("a", "APP", reader),
        project("b", "API", fullAccess(areas)),
      ]);

      expect(draft.individual).toBe(true);
      expect(accessPayload(areas, draft)).toEqual({
        projectIds: ["a", "b"],
        projectPermissions: { a: reader, b: fullAccess(areas) },
      });
    });

    it("summarises projects that agree once and the others one by one", () => {
      expect(
        summarizeProjectPermissions(areas, [
          project("a", "APP", reader),
          project("b", "API", reader),
        ]),
      ).toEqual([{ badges: ["Tasks · Read"] }]);

      expect(
        summarizeProjectPermissions(areas, [
          project("a", "APP", reader),
          project("b", "API", fullAccess(areas)),
        ]),
      ).toEqual([
        { project: "APP · APP", badges: ["Tasks · Read"] },
        { project: "API · API", badges: ["Full access"] },
      ]);

      expect(summarizeProjectPermissions(areas, [])).toEqual([]);
    });
  });
});
