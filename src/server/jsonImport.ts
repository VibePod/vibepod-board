import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import type { Pool } from "pg";

import type {
  BoardCard,
  BoardData,
  Idea,
  PlanDocument,
  Project,
} from "../shared/types.js";

const defaultProjectSummary = "Default project for uncategorized work.";
const projectKeyPattern = /^[A-Z]{1,3}$/;

type LegacyIdea = Omit<Idea, "projectId" | "taskNumber" | "status"> & {
  projectId?: string;
  taskNumber?: number;
  status: string;
};
type LegacyBoardCard = Omit<BoardCard, "projectId"> & { projectId?: string };
type LegacyPlanDocument = Omit<PlanDocument, "projectId"> & {
  projectId?: string;
};
type PersistedBoardData = Partial<
  Omit<
    BoardData,
    "schemaVersion" | "projects" | "ideas" | "boardCards" | "documents"
  >
> & {
  schemaVersion?: number;
  projects?: Partial<Project>[];
  ideas?: LegacyIdea[];
  boardCards?: LegacyBoardCard[];
  documents?: LegacyPlanDocument[];
};

const emptyData = (): BoardData => ({
  schemaVersion: 3,
  projects: [],
  ideas: [],
  boardCards: [],
  readinessEvents: [],
  documents: [],
  activity: [],
});

const nowIso = () => new Date().toISOString();

export const normalizeImportedBoardData = (
  input: PersistedBoardData,
): BoardData => {
  const normalizedProjects = normalizeProjects(input.projects);
  const data: BoardData = {
    ...emptyData(),
    projects: normalizedProjects.projects,
    ideas: (input.ideas ?? []) as Idea[],
    boardCards: (input.boardCards ?? []) as BoardCard[],
    readinessEvents: input.readinessEvents ?? [],
    documents: (input.documents ?? []) as PlanDocument[],
    activity: input.activity ?? [],
  };

  migrateProjectOwnership(data);
  normalizeIdeaStatuses(data);
  normalizeIdeaTaskNumbers(data);

  return data;
};

export const importBoardJsonFile = async (
  pool: Pool,
  filePath: string,
  options: { allowNonEmpty?: boolean } = {},
): Promise<void> => {
  const parsed = JSON.parse(
    await readFile(filePath, "utf8"),
  ) as PersistedBoardData;
  const data = normalizeImportedBoardData(parsed);
  const client = await pool.connect();

  try {
    await client.query("begin");
    if (!options.allowNonEmpty) {
      const existing = await client.query<{ count: string }>(
        `select sum(row_count)::text as count
         from (
           select count(*) as row_count from projects
           union all select count(*) from ideas
           union all select count(*) from board_cards
           union all select count(*) from documents
           union all select count(*) from activity_events
         ) counts`,
      );
      if (Number(existing.rows[0]?.count ?? 0) > 0) {
        throw new Error("PostgreSQL board tables are not empty");
      }
    }

    for (const project of data.projects) {
      await client.query(
        `insert into projects (id, key, title, summary, created_at, updated_at)
         values ($1, $2, $3, $4, $5, $6)`,
        [
          project.id,
          project.key,
          project.title,
          project.summary,
          project.createdAt,
          project.updatedAt,
        ],
      );
    }

    for (const idea of data.ideas) {
      await client.query(
        `insert into ideas (
           id, project_id, task_number, title, summary, details, status, labels,
           acceptance_criteria, github_issue_url, github_issue_number, repository_local_path,
           repository_remote_url, assignee, readiness_score, readiness_reason,
           readiness_evaluated_at, created_at, updated_at
         )
         values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)`,
        [
          idea.id,
          idea.projectId,
          idea.taskNumber,
          idea.title,
          idea.summary,
          idea.details,
          idea.status,
          JSON.stringify(idea.labels),
          JSON.stringify(idea.acceptanceCriteria),
          idea.githubIssueUrl ?? null,
          idea.githubIssueNumber ?? null,
          idea.repositoryLocalPath ?? null,
          idea.repositoryRemoteUrl ?? null,
          idea.assignee ?? null,
          idea.readinessScore ?? null,
          idea.readinessReason ?? null,
          idea.readinessEvaluatedAt ?? null,
          idea.createdAt,
          idea.updatedAt,
        ],
      );
    }

    for (const card of data.boardCards) {
      await client.query(
        `insert into board_cards (
           id, project_id, idea_id, title, details, column_name, branch_name, github_issue_url,
           github_issue_number, repository_local_path, repository_remote_url, assignee, labels,
           readiness_score, readiness_reason, readiness_evaluated_at, created_at, updated_at
         )
         values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)`,
        [
          card.id,
          card.projectId,
          card.ideaId ?? null,
          card.title,
          card.details,
          card.column,
          card.branchName ?? null,
          card.githubIssueUrl ?? null,
          card.githubIssueNumber ?? null,
          card.repositoryLocalPath ?? null,
          card.repositoryRemoteUrl ?? null,
          card.assignee ?? null,
          JSON.stringify(card.labels),
          card.readinessScore ?? null,
          card.readinessReason ?? null,
          card.readinessEvaluatedAt ?? null,
          card.createdAt,
          card.updatedAt,
        ],
      );
    }

    for (const document of data.documents) {
      await client.query(
        `insert into documents (
           id, project_id, title, kind, content, linked_idea_ids, linked_card_ids, created_at, updated_at
         )
         values ($1, $2, $3, $4, $5, $6, $7, $8, $9)`,
        [
          document.id,
          document.projectId,
          document.title,
          document.kind,
          document.content,
          JSON.stringify(document.linkedIdeaIds),
          JSON.stringify(document.linkedCardIds),
          document.createdAt,
          document.updatedAt,
        ],
      );
    }

    const ideaProjects = new Map(
      data.ideas.map((idea) => [idea.id, idea.projectId]),
    );
    for (const idea of data.ideas) {
      for (const dependsOnId of new Set(idea.dependsOn ?? [])) {
        // Skip edges that point outside the import or across projects; they
        // cannot be represented and would fail the foreign key anyway.
        if (
          dependsOnId === idea.id ||
          ideaProjects.get(dependsOnId) !== idea.projectId
        ) {
          continue;
        }
        await client.query(
          `insert into task_dependencies (idea_id, depends_on_idea_id, created_at)
           values ($1, $2, $3)
           on conflict do nothing`,
          [idea.id, dependsOnId, idea.updatedAt],
        );
      }
    }

    const importedIdeaIds = new Set(data.ideas.map((idea) => idea.id));
    for (const event of data.readinessEvents) {
      if (!importedIdeaIds.has(event.ideaId)) {
        continue;
      }
      await client.query(
        `insert into idea_readiness_events (id, idea_id, score, reason, created_at)
         values ($1, $2, $3, $4, $5)`,
        [event.id, event.ideaId, event.score, event.reason, event.createdAt],
      );
    }

    for (const event of data.activity) {
      await client.query(
        "insert into activity_events (id, type, message, created_at) values ($1, $2, $3, $4)",
        [event.id, event.type, event.message, event.createdAt],
      );
    }

    await client.query("commit");
  } catch (error) {
    await client.query("rollback");
    throw error;
  } finally {
    client.release();
  }
};

const normalizeProjects = (projects: Partial<Project>[] | undefined) => {
  let migrated = false;
  const normalized: Project[] = [];
  const usedKeys = new Set<string>();
  for (const project of projects ?? []) {
    if (!project.id || !project.title?.trim()) {
      migrated = true;
      continue;
    }
    const timestamp = project.updatedAt ?? project.createdAt ?? nowIso();
    const requestedKey = normalizedProjectKeyOrUndefined(project.key);
    const key =
      requestedKey && !usedKeys.has(requestedKey)
        ? requestedKey
        : nextAvailableProjectKey(usedKeys, projectKeyFromTitle(project.title));
    usedKeys.add(key);
    normalized.push({
      id: project.id,
      key,
      title: project.title.trim(),
      summary: project.summary?.trim() ?? "",
      createdAt: project.createdAt ?? timestamp,
      updatedAt: project.updatedAt ?? timestamp,
    });
    if (
      !project.createdAt ||
      !project.updatedAt ||
      project.title !== project.title.trim() ||
      project.summary !== project.summary?.trim() ||
      project.key !== key
    ) {
      migrated = true;
    }
  }
  return { projects: normalized, migrated };
};

const migrateProjectOwnership = (data: BoardData) => {
  let migrated = false;
  const hasUnownedWork =
    data.ideas.some((idea) => !idea.projectId) ||
    data.boardCards.some((card) => !card.projectId) ||
    data.documents.some((document) => !document.projectId);

  if (hasUnownedWork && data.projects.length === 0) {
    const timestamp = nowIso();
    data.projects.push({
      id: randomUUID(),
      key: nextAvailableProjectKey(
        data.projects.map((project) => project.key),
        "GEN",
      ),
      title: "General",
      summary: defaultProjectSummary,
      createdAt: timestamp,
      updatedAt: timestamp,
    });
    migrated = true;
  }

  const fallbackProjectId = data.projects[0]?.id;
  if (!fallbackProjectId) {
    return migrated;
  }

  const ideaProjectIds = new Map(
    data.ideas.map((idea) => [idea.id, idea.projectId || fallbackProjectId]),
  );
  const cardProjectIds = new Map(
    data.boardCards.map((card) => [
      card.id,
      card.projectId ||
        (card.ideaId ? ideaProjectIds.get(card.ideaId) : undefined) ||
        fallbackProjectId,
    ]),
  );

  for (const idea of data.ideas) {
    if (!idea.projectId) {
      idea.projectId = fallbackProjectId;
      migrated = true;
    }
  }

  for (const card of data.boardCards) {
    if (!card.projectId) {
      card.projectId = cardProjectIds.get(card.id) ?? fallbackProjectId;
      migrated = true;
    }
  }

  for (const document of data.documents) {
    if (!document.projectId) {
      document.projectId =
        document.linkedIdeaIds
          .map((id) => ideaProjectIds.get(id))
          .find(Boolean) ??
        document.linkedCardIds
          .map((id) => cardProjectIds.get(id))
          .find(Boolean) ??
        fallbackProjectId;
      migrated = true;
    }
  }

  return migrated;
};

const normalizeIdeaTaskNumbers = (data: BoardData) => {
  let migrated = false;
  for (const project of data.projects) {
    const ideas = data.ideas
      .filter((idea) => idea.projectId === project.id)
      .sort(
        (a, b) =>
          a.createdAt.localeCompare(b.createdAt) ||
          a.updatedAt.localeCompare(b.updatedAt) ||
          a.id.localeCompare(b.id),
      );
    const usedNumbers = new Set<number>();
    let nextNumber = 1;

    for (const idea of ideas) {
      const currentNumber =
        Number.isInteger(idea.taskNumber) && idea.taskNumber > 0
          ? idea.taskNumber
          : undefined;
      if (currentNumber && !usedNumbers.has(currentNumber)) {
        usedNumbers.add(currentNumber);
        nextNumber = Math.max(nextNumber, currentNumber + 1);
        continue;
      }

      while (usedNumbers.has(nextNumber)) {
        nextNumber += 1;
      }
      idea.taskNumber = nextNumber;
      usedNumbers.add(nextNumber);
      nextNumber += 1;
      migrated = true;
    }
  }
  return migrated;
};

const normalizeIdeaStatuses = (data: BoardData) => {
  let migrated = false;
  for (const idea of data.ideas) {
    if ((idea.status as string) === "synced") {
      idea.status = "ready";
      migrated = true;
    }
    if ((idea.status as string) === "dennied") {
      idea.status = "denied";
      migrated = true;
    }
  }
  return migrated;
};

const normalizedProjectKeyOrUndefined = (key: unknown): string | undefined => {
  if (typeof key !== "string") {
    return undefined;
  }
  const normalized = key.trim();
  return projectKeyPattern.test(normalized) ? normalized : undefined;
};

const projectKeyFromTitle = (title: string): string => {
  const letters = title
    .split(/\s+/)
    .map((part) => part.replace(/[^A-Za-z]/g, ""))
    .filter(Boolean)
    .map((part) => part[0].toUpperCase())
    .join("")
    .slice(0, 3);
  return letters || "PRJ";
};

const nextAvailableProjectKey = (
  usedKeysInput: Iterable<string | undefined>,
  preferred = "PRJ",
): string => {
  const usedKeys = new Set<string>();
  for (const key of usedKeysInput) {
    const normalized = normalizedProjectKeyOrUndefined(key);
    if (normalized) {
      usedKeys.add(normalized);
    }
  }

  const preferredKey = normalizedProjectKeyOrUndefined(preferred) ?? "PRJ";
  if (!usedKeys.has(preferredKey)) {
    return preferredKey;
  }

  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  for (const first of alphabet) {
    if (!usedKeys.has(first)) {
      return first;
    }
  }
  for (const first of alphabet) {
    for (const second of alphabet) {
      const key = `${first}${second}`;
      if (!usedKeys.has(key)) {
        return key;
      }
    }
  }
  for (const first of alphabet) {
    for (const second of alphabet) {
      for (const third of alphabet) {
        const key = `${first}${second}${third}`;
        if (!usedKeys.has(key)) {
          return key;
        }
      }
    }
  }

  throw new Error("No available project IDs");
};
