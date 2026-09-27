import { describe, expect, it } from "vitest";

import { parseProjectBundle } from "../src/shared/projectBundle.js";
import type { ProjectBundle } from "../src/shared/types.js";

const timestamp = "2026-08-14T12:00:00.000Z";

const validBundle = (): ProjectBundle => ({
  bundleVersion: 1,
  exportedAt: timestamp,
  project: {
    id: "source-project",
    key: "APP",
    title: "Application",
    summary: "Portable app project",
    createdAt: timestamp,
    updatedAt: timestamp,
  },
  ideas: [
    {
      id: "idea-1",
      projectId: "source-project",
      taskNumber: 1,
      title: "Foundation",
      summary: "",
      details: "",
      status: "ready",
      labels: [],
      acceptanceCriteria: [],
      dependsOn: [],
      blocks: ["idea-2"],
      blockedBy: [],
      createdAt: timestamp,
      updatedAt: timestamp,
    },
    {
      id: "idea-2",
      projectId: "source-project",
      taskNumber: 2,
      title: "Feature",
      summary: "",
      details: "",
      status: "idea",
      labels: ["ui"],
      acceptanceCriteria: ["Works"],
      dependsOn: ["idea-1"],
      blocks: [],
      blockedBy: ["idea-1"],
      createdAt: timestamp,
      updatedAt: timestamp,
    },
  ],
  boardCards: [
    {
      id: "card-1",
      projectId: "source-project",
      ideaId: "idea-1",
      title: "Foundation",
      details: "",
      column: "planned",
      labels: [],
      dependsOn: [],
      blockedBy: [],
      createdAt: timestamp,
      updatedAt: timestamp,
    },
  ],
  readinessEvents: [
    {
      id: "readiness-1",
      ideaId: "idea-1",
      score: 8,
      reason: "Clear",
      createdAt: timestamp,
    },
  ],
  documents: [
    {
      id: "document-1",
      projectId: "source-project",
      title: "Plan",
      kind: "execution_plan",
      content: "Ship it",
      linkedIdeaIds: ["idea-1"],
      linkedCardIds: ["card-1"],
      createdAt: timestamp,
      updatedAt: timestamp,
    },
  ],
});

describe("project bundle validation", () => {
  it("accepts a self-contained V1 bundle", () => {
    expect(parseProjectBundle(validBundle())).toEqual(validBundle());
  });

  const invalidCases: {
    name: string;
    mutate: (bundle: ProjectBundle) => unknown;
  }[] = [
    {
      name: "unsupported version",
      mutate: (bundle) => ({ ...bundle, bundleVersion: 99 }),
    },
    {
      name: "foreign idea",
      mutate: (bundle) => ({
        ...bundle,
        ideas: [{ ...bundle.ideas[0], projectId: "other" }, bundle.ideas[1]],
      }),
    },
    {
      name: "missing dependency",
      mutate: (bundle) => ({
        ...bundle,
        ideas: [
          bundle.ideas[0],
          { ...bundle.ideas[1], dependsOn: ["missing"] },
        ],
      }),
    },
    {
      name: "dependency cycle",
      mutate: (bundle) => ({
        ...bundle,
        ideas: [{ ...bundle.ideas[0], dependsOn: ["idea-2"] }, bundle.ideas[1]],
      }),
    },
    {
      name: "foreign card link",
      mutate: (bundle) => ({
        ...bundle,
        boardCards: [{ ...bundle.boardCards[0], ideaId: "missing" }],
      }),
    },
    {
      name: "foreign document link",
      mutate: (bundle) => ({
        ...bundle,
        documents: [{ ...bundle.documents[0], linkedIdeaIds: ["missing"] }],
      }),
    },
    {
      name: "duplicate task number",
      mutate: (bundle) => ({
        ...bundle,
        ideas: [bundle.ideas[0], { ...bundle.ideas[1], taskNumber: 1 }],
      }),
    },
  ];

  it.each(invalidCases)("rejects $name", ({ mutate }) => {
    expect(() => parseProjectBundle(mutate(validBundle()))).toThrow();
  });

  it("accepts a bundle without the derived dependency fields", () => {
    const bundle = validBundle();
    const stripped = {
      ...bundle,
      ideas: bundle.ideas.map(({ blocks, blockedBy, ...idea }) => idea),
      boardCards: bundle.boardCards.map(
        ({ dependsOn, blockedBy, ...card }) => card,
      ),
    };

    expect(parseProjectBundle(stripped)).toMatchObject({
      ideas: [
        { id: "idea-1", blocks: [], blockedBy: [] },
        { id: "idea-2", blocks: [], blockedBy: [], dependsOn: ["idea-1"] },
      ],
      boardCards: [{ id: "card-1", dependsOn: [], blockedBy: [] }],
    });
  });

  it("accepts GitHub sync state in version 2 bundles only", () => {
    const linked = (bundleVersion: 1 | 2) => {
      const bundle = validBundle();
      return {
        ...bundle,
        bundleVersion,
        ideas: [
          {
            ...bundle.ideas[0],
            githubIssueUrl: "https://github.com/o/r/issues/1",
            githubIssueNumber: 1,
            githubRepository: "o/r",
            githubIssueState: "open" as const,
          },
          bundle.ideas[1],
        ],
      };
    };

    expect(parseProjectBundle(linked(2)).ideas[0].githubRepository).toBe("o/r");
    expect(() => parseProjectBundle(linked(1))).toThrow(
      "GitHub sync fields require bundleVersion 2",
    );
  });

  it("rejects two tasks linked to the same GitHub issue", () => {
    const bundle = validBundle();
    const link = {
      githubIssueUrl: "https://github.com/o/r/issues/1",
      githubIssueNumber: 1,
    };
    expect(() =>
      parseProjectBundle({
        ...bundle,
        bundleVersion: 2,
        ideas: [
          { ...bundle.ideas[0], ...link, githubRepository: "O/R" },
          { ...bundle.ideas[1], ...link, githubRepository: "o/r" },
        ],
      }),
    ).toThrow("Duplicate GitHub issue link: o/r#1");
  });

  it("rejects unknown fields", () => {
    expect(() =>
      parseProjectBundle({ ...validBundle(), mystery: true }),
    ).toThrow();
  });
});
