import { z } from "zod";

import { dependencyMap, findCyclicTaskIds } from "./dependencies.js";
import {
  boardColumns,
  documentKinds,
  ideaStatuses,
  type ProjectBundle,
} from "./types.js";

const timestampSchema = z.string().datetime({ offset: true });
const optionalTextSchema = z.string().optional();
const optionalIntegerSchema = z.number().int().optional();
const optionalReadinessScoreSchema = z.number().int().min(1).max(10).optional();

const projectSchema = z
  .object({
    id: z.string().min(1),
    key: z.string().regex(/^[A-Z]{1,3}$/),
    title: z.string().trim().min(1),
    summary: z.string(),
    createdAt: timestampSchema,
    updatedAt: timestampSchema,
  })
  .strict();

const ideaSchema = z
  .object({
    id: z.string().min(1),
    projectId: z.string().min(1),
    taskNumber: z.number().int().positive(),
    title: z.string().trim().min(1),
    summary: z.string(),
    details: z.string(),
    status: z.enum(ideaStatuses),
    labels: z.array(z.string()),
    acceptanceCriteria: z.array(z.string()),
    dependsOn: z.array(z.string().min(1)),
    blocks: z.array(z.string().min(1)),
    blockedBy: z.array(z.string().min(1)),
    githubIssueUrl: optionalTextSchema,
    githubIssueNumber: optionalIntegerSchema,
    repositoryLocalPath: optionalTextSchema,
    repositoryRemoteUrl: optionalTextSchema,
    assignee: optionalTextSchema,
    readinessScore: optionalReadinessScoreSchema,
    readinessReason: optionalTextSchema,
    readinessEvaluatedAt: timestampSchema.optional(),
    createdAt: timestampSchema,
    updatedAt: timestampSchema,
  })
  .strict();

const boardCardSchema = z
  .object({
    id: z.string().min(1),
    projectId: z.string().min(1),
    title: z.string().trim().min(1),
    details: z.string(),
    column: z.enum(boardColumns),
    branchName: optionalTextSchema,
    ideaId: optionalTextSchema,
    githubIssueUrl: optionalTextSchema,
    githubIssueNumber: optionalIntegerSchema,
    repositoryLocalPath: optionalTextSchema,
    repositoryRemoteUrl: optionalTextSchema,
    assignee: optionalTextSchema,
    labels: z.array(z.string()),
    dependsOn: z.array(z.string().min(1)),
    blockedBy: z.array(z.string().min(1)),
    readinessScore: optionalReadinessScoreSchema,
    readinessReason: optionalTextSchema,
    readinessEvaluatedAt: timestampSchema.optional(),
    createdAt: timestampSchema,
    updatedAt: timestampSchema,
  })
  .strict();

const readinessEventSchema = z
  .object({
    id: z.string().min(1),
    ideaId: z.string().min(1),
    score: z.number().int().min(1).max(10),
    reason: z.string(),
    createdAt: timestampSchema,
  })
  .strict();

const documentSchema = z
  .object({
    id: z.string().min(1),
    projectId: z.string().min(1),
    title: z.string().trim().min(1),
    kind: z.enum(documentKinds),
    content: z.string(),
    linkedIdeaIds: z.array(z.string().min(1)),
    linkedCardIds: z.array(z.string().min(1)),
    createdAt: timestampSchema,
    updatedAt: timestampSchema,
  })
  .strict();

const addIssue = (
  ctx: z.RefinementCtx,
  path: (string | number)[],
  message: string,
) => {
  ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
};

const duplicateValues = <T>(values: T[]): Set<T> => {
  const seen = new Set<T>();
  const duplicates = new Set<T>();
  for (const value of values) {
    if (seen.has(value)) {
      duplicates.add(value);
    }
    seen.add(value);
  }
  return duplicates;
};

/**
 * Same rule the store applies when it orders work, so a bundle can never
 * validate here and then fail to order after import.
 */
const cyclicIdeaIds = (bundle: ProjectBundle): string[] =>
  findCyclicTaskIds(dependencyMap(bundle.ideas));

const validateRelationships = (bundle: ProjectBundle, ctx: z.RefinementCtx) => {
  const ideaIds = new Set(bundle.ideas.map((idea) => idea.id));
  const cardIds = new Set(bundle.boardCards.map((card) => card.id));

  for (const id of duplicateValues(bundle.ideas.map((idea) => idea.id))) {
    addIssue(ctx, ["ideas"], `Duplicate idea id: ${id}`);
  }
  for (const taskNumber of duplicateValues(
    bundle.ideas.map((idea) => idea.taskNumber),
  )) {
    addIssue(ctx, ["ideas"], `Duplicate task number: ${taskNumber}`);
  }
  for (const id of duplicateValues(bundle.boardCards.map((card) => card.id))) {
    addIssue(ctx, ["boardCards"], `Duplicate board card id: ${id}`);
  }
  for (const id of duplicateValues(
    bundle.readinessEvents.map((event) => event.id),
  )) {
    addIssue(ctx, ["readinessEvents"], `Duplicate readiness event id: ${id}`);
  }
  for (const id of duplicateValues(
    bundle.documents.map((document) => document.id),
  )) {
    addIssue(ctx, ["documents"], `Duplicate document id: ${id}`);
  }

  bundle.ideas.forEach((idea, index) => {
    if (idea.projectId !== bundle.project.id) {
      addIssue(
        ctx,
        ["ideas", index, "projectId"],
        "Idea belongs to another project",
      );
    }
    for (const dependencyId of idea.dependsOn) {
      if (!ideaIds.has(dependencyId)) {
        addIssue(
          ctx,
          ["ideas", index, "dependsOn"],
          `Dependency is not in bundle: ${dependencyId}`,
        );
      }
    }
  });

  for (const id of cyclicIdeaIds(bundle)) {
    addIssue(ctx, ["ideas"], `Dependency cycle includes: ${id}`);
  }

  bundle.boardCards.forEach((card, index) => {
    if (card.projectId !== bundle.project.id) {
      addIssue(
        ctx,
        ["boardCards", index, "projectId"],
        "Board card belongs to another project",
      );
    }
    if (card.ideaId && !ideaIds.has(card.ideaId)) {
      addIssue(
        ctx,
        ["boardCards", index, "ideaId"],
        `Linked idea is not in bundle: ${card.ideaId}`,
      );
    }
  });

  bundle.readinessEvents.forEach((event, index) => {
    if (!ideaIds.has(event.ideaId)) {
      addIssue(
        ctx,
        ["readinessEvents", index, "ideaId"],
        `Readiness idea is not in bundle: ${event.ideaId}`,
      );
    }
  });

  bundle.documents.forEach((document, index) => {
    if (document.projectId !== bundle.project.id) {
      addIssue(
        ctx,
        ["documents", index, "projectId"],
        "Document belongs to another project",
      );
    }
    for (const ideaId of document.linkedIdeaIds) {
      if (!ideaIds.has(ideaId)) {
        addIssue(
          ctx,
          ["documents", index, "linkedIdeaIds"],
          `Linked idea is not in bundle: ${ideaId}`,
        );
      }
    }
    for (const cardId of document.linkedCardIds) {
      if (!cardIds.has(cardId)) {
        addIssue(
          ctx,
          ["documents", index, "linkedCardIds"],
          `Linked card is not in bundle: ${cardId}`,
        );
      }
    }
  });
};

export const projectBundleSchema = z
  .object({
    bundleVersion: z.literal(1),
    exportedAt: timestampSchema,
    project: projectSchema,
    ideas: z.array(ideaSchema),
    boardCards: z.array(boardCardSchema),
    readinessEvents: z.array(readinessEventSchema),
    documents: z.array(documentSchema),
  })
  .strict()
  .superRefine(validateRelationships);

export const parseProjectBundle = (input: unknown): ProjectBundle =>
  projectBundleSchema.parse(input);
