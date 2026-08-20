// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  downloadResponse,
  parseProjectBundleText,
  projectBundlePreview,
  projectExportFileName,
} from "../src/client/projectTransfer.js";
import type { ProjectBundle } from "../src/shared/types.js";

const timestamp = "2026-08-14T12:00:00.000Z";
const bundle: ProjectBundle = {
  bundleVersion: 1,
  exportedAt: timestamp,
  project: {
    id: "source-project",
    key: "APP",
    title: "Application",
    summary: "",
    createdAt: timestamp,
    updatedAt: timestamp,
  },
  ideas: [
    {
      id: "idea-1",
      projectId: "source-project",
      taskNumber: 1,
      title: "One",
      summary: "",
      details: "",
      status: "idea",
      labels: [],
      acceptanceCriteria: [],
      dependsOn: [],
      blocks: [],
      blockedBy: [],
      createdAt: timestamp,
      updatedAt: timestamp,
    },
    {
      id: "idea-2",
      projectId: "source-project",
      taskNumber: 2,
      title: "Two",
      summary: "",
      details: "",
      status: "idea",
      labels: [],
      acceptanceCriteria: [],
      dependsOn: [],
      blocks: [],
      blockedBy: [],
      createdAt: timestamp,
      updatedAt: timestamp,
    },
  ],
  boardCards: [
    {
      id: "card-1",
      projectId: "source-project",
      title: "One",
      details: "",
      column: "ready",
      ideaId: "idea-1",
      labels: [],
      dependsOn: [],
      blockedBy: [],
      createdAt: timestamp,
      updatedAt: timestamp,
    },
  ],
  readinessEvents: [],
  documents: [
    {
      id: "document-1",
      projectId: "source-project",
      title: "Plan",
      kind: "execution_plan",
      content: "",
      linkedIdeaIds: ["idea-1"],
      linkedCardIds: ["card-1"],
      createdAt: timestamp,
      updatedAt: timestamp,
    },
  ],
};

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("project transfer browser helpers", () => {
  it("parses and validates project bundle JSON", () => {
    expect(parseProjectBundleText(JSON.stringify(bundle))).toEqual(bundle);
    expect(() => parseProjectBundleText("not JSON")).toThrow(
      "Project file is not valid JSON",
    );
    expect(() =>
      parseProjectBundleText(JSON.stringify({ bundleVersion: 99 })),
    ).toThrow("Project file is invalid");
  });

  it("builds a preview and matches the destination by project key", () => {
    expect(
      projectBundlePreview(bundle, [
        { ...bundle.project, id: "destination-project" },
      ]),
    ).toEqual({
      key: "APP",
      title: "Application",
      tasks: 2,
      cards: 1,
      documents: 1,
      existingProjectId: "destination-project",
    });
    expect(projectBundlePreview(bundle, [])).toMatchObject({
      existingProjectId: undefined,
    });
    expect(projectExportFileName(bundle.project)).toBe("APP-project.json");
  });

  it("downloads a response blob and cleans up the temporary URL", async () => {
    const createObjectUrl = vi.fn(() => "blob:project-export");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectUrl,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectUrl,
    });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    await downloadResponse(
      new Response(JSON.stringify(bundle), {
        headers: { "Content-Type": "application/json" },
      }),
      "APP-project.json",
    );

    expect(createObjectUrl).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(click.mock.instances[0]).toMatchObject({
      href: "blob:project-export",
      download: "APP-project.json",
    });
    // Revocation is deferred so the browser can start the download first.
    expect(revokeObjectUrl).not.toHaveBeenCalled();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:project-export");
    expect(document.querySelector("a[download]")).toBeNull();
  });
});
