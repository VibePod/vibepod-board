import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import type { Pool, PoolClient } from "pg";

import {
  buildWorkOrder,
  findCyclicTaskIds,
  isTaskComplete,
  normalizeDependencyIds,
  type TaskWorkOrder,
} from "../shared/dependencies.js";
import { parseProjectBundle } from "../shared/projectBundle.js";
import {
  type ActivityEvent,
  type ApiTokenSummary,
  type BoardCard,
  type BoardColumn,
  type BoardColumns,
  type BoardData,
  boardColumns,
  type CreateApiTokenInput,
  type CreateBoardCardOptions,
  type CreateDocumentInput,
  type CreateIdeaInput,
  type CreateProjectInput,
  type Idea,
  type ImportProjectOptions,
  type ImportProjectResult,
  type PlanDocument,
  type Project,
  type ProjectBundle,
  type ReadinessEvent,
  type SetCardReadinessInput,
  type UpdateApiTokenInput,
  type UpdateBoardCardInput,
  type UpdateDocumentInput,
  type UpdateIdeaInput,
  type UpdateProjectInput,
} from "../shared/types.js";
import { createRawApiToken, hashApiToken } from "./auth.js";
import {
  type AccessContext,
  type AuthenticatedToken,
  assertCanAccessProject,
  type BoardDataStore,
  type CreatedApiToken,
  defaultProjectIdForCreate,
  filterProjectIds,
  tokenAccess,
} from "./store.js";

const emptyData = (): BoardData => ({
  schemaVersion: 3,
  projects: [],
  ideas: [],
  boardCards: [],
  readinessEvents: [],
  documents: [],
  activity: [],
});

type LegacyIdea = Omit<Idea, "projectId" | "taskNumber"> & {
  projectId?: string;
  taskNumber?: number;
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

type LoadResult = {
  data: BoardData;
  migrated: boolean;
};

const defaultProjectSummary = "Default project for uncategorized work.";
const projectKeyPattern = /^[A-Z]{1,3}$/;

const nowIso = () => new Date().toISOString();

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

const normalizeList = (items: string[] | undefined): string[] => [
  ...new Set((items ?? []).map((item) => item.trim()).filter(Boolean)),
];

const normalizeOptionalText = (
  value: string | undefined,
): string | undefined => {
  if (value === undefined) {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed || undefined;
};

const normalizeReadinessInput = (
  input: SetCardReadinessInput,
): { score: number; reason: string } => {
  if (!Number.isInteger(input.score) || input.score < 1 || input.score > 10) {
    throw new Error("Readiness score must be an integer from 1 to 10");
  }
  const reason = input.reason?.trim() ?? "";
  if (!reason) {
    throw new Error("Readiness reason is required");
  }
  return { score: input.score, reason };
};

const assertTitle = (title: string | undefined, entity: string) => {
  if (!title?.trim()) {
    throw new Error(`${entity} title is required`);
  }
};

const normalizeProjectKey = (key: string | undefined): string => {
  if (!key?.trim()) {
    throw new Error("Project ID is required");
  }
  const normalized = key.trim();
  if (!projectKeyPattern.test(normalized)) {
    throw new Error("Project ID must be 1 to 3 capital letters");
  }
  return normalized;
};

type ProjectRow = {
  id: string;
  key: string;
  title: string;
  summary: string;
  created_at: Date | string;
  updated_at: Date | string;
};

type IdeaRow = {
  id: string;
  project_id: string;
  task_number: number;
  title: string;
  summary: string;
  details: string;
  status: Idea["status"];
  labels: unknown;
  acceptance_criteria: unknown;
  github_issue_url: string | null;
  github_issue_number: number | null;
  repository_local_path: string | null;
  repository_remote_url: string | null;
  readiness_score: number | null;
  readiness_reason: string | null;
  readiness_evaluated_at: Date | string | null;
  created_at: Date | string;
  updated_at: Date | string;
};

type ReadinessEventRow = {
  id: string;
  idea_id: string;
  score: number;
  reason: string;
  created_at: Date | string;
};

type BoardCardRow = {
  id: string;
  project_id: string;
  idea_id: string | null;
  title: string;
  details: string;
  column_name: BoardColumn;
  branch_name: string | null;
  github_issue_url: string | null;
  github_issue_number: number | null;
  repository_local_path: string | null;
  repository_remote_url: string | null;
  labels: unknown;
  readiness_score: number | null;
  readiness_reason: string | null;
  readiness_evaluated_at: Date | string | null;
  created_at: Date | string;
  updated_at: Date | string;
};

type DocumentRow = {
  id: string;
  project_id: string;
  title: string;
  kind: PlanDocument["kind"];
  content: string;
  linked_idea_ids: unknown;
  linked_card_ids: unknown;
  created_at: Date | string;
  updated_at: Date | string;
};

type DependencyRow = {
  idea_id: string;
  depends_on_idea_id: string;
};

type TaskStateRow = {
  id: string;
  status: Idea["status"];
  column_name: BoardColumn | null;
};

type ActivityRow = {
  id: string;
  type: string;
  message: string;
  created_at: Date | string;
};

type ApiTokenRow = {
  id: string;
  name: string;
  created_at: Date | string;
  last_used_at: Date | string | null;
  revoked_at: Date | string | null;
};

export class PostgresBoardStore implements BoardDataStore {
  constructor(private readonly pool: Pool) {}

  async getState(access: AccessContext): Promise<BoardData> {
    const ideas = await this.listIdeas(access);
    const events = ideas.length
      ? await this.pool.query<ReadinessEventRow>(
          "select * from idea_readiness_events where idea_id = any($1::text[]) order by created_at desc",
          [ideas.map((idea) => idea.id)],
        )
      : { rows: [] as ReadinessEventRow[] };
    return {
      schemaVersion: 3,
      projects: await this.listProjects(access),
      ideas,
      boardCards: await this.listBoardCards(access),
      readinessEvents: events.rows.map(readinessEventFromRow),
      documents: await this.listDocuments(access),
      activity: await this.listActivity(access),
    };
  }

  async exportProject(id: string): Promise<ProjectBundle> {
    const project = await this.requireProject(this.pool, id);
    const access = tokenAccess("project-export", [project.id]);
    const ideas = await this.listIdeas(access, project.id);
    const events = ideas.length
      ? await this.pool.query<ReadinessEventRow>(
          "select * from idea_readiness_events where idea_id = any($1::text[]) order by created_at desc",
          [ideas.map((idea) => idea.id)],
        )
      : { rows: [] as ReadinessEventRow[] };

    return parseProjectBundle({
      bundleVersion: 1,
      exportedAt: nowIso(),
      project,
      ideas,
      boardCards: await this.listBoardCards(access, project.id),
      readinessEvents: events.rows.map(readinessEventFromRow),
      documents: await this.listDocuments(access, project.id),
    });
  }

  async importProject(
    input: ProjectBundle,
    options: ImportProjectOptions,
  ): Promise<ImportProjectResult> {
    const bundle = parseProjectBundle(input);
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      // `for update` serialises concurrent imports of the same key. Without it
      // both transactions read the same state under read committed and the
      // second one dies on a unique constraint instead of waiting its turn.
      const existingByKey = await client.query<ProjectRow>(
        "select * from projects where key = $1 for update",
        [bundle.project.key],
      );
      const existing = existingByKey.rows[0];
      if (existing && !options.replaceExisting) {
        throw new Error(
          `Project key ${bundle.project.key} already exists; replacement confirmation is required`,
        );
      }

      const destinationProjectId = existing?.id ?? bundle.project.id;
      if (!existing) {
        const idCollision = await client.query<ProjectRow>(
          "select * from projects where id = $1",
          [bundle.project.id],
        );
        if (idCollision.rows[0]) {
          throw new Error(`Project ID is already used: ${bundle.project.id}`);
        }
      }
      await this.assertImportedIdsAvailable(
        client,
        bundle,
        destinationProjectId,
      );

      if (existing) {
        await client.query("delete from board_cards where project_id = $1", [
          destinationProjectId,
        ]);
        await client.query("delete from documents where project_id = $1", [
          destinationProjectId,
        ]);
        await client.query("delete from ideas where project_id = $1", [
          destinationProjectId,
        ]);
        await client.query(
          `update projects
           set title = $2, summary = $3, created_at = $4, updated_at = $5
           where id = $1`,
          [
            destinationProjectId,
            bundle.project.title,
            bundle.project.summary,
            bundle.project.createdAt,
            bundle.project.updatedAt,
          ],
        );
        await this.insertImportedChildren(client, bundle, destinationProjectId);
      } else {
        await this.insertImportedProject(client, bundle, destinationProjectId);
      }
      await this.addActivity(
        client,
        "project.imported",
        `Imported project: ${bundle.project.title}`,
        nowIso(),
      );
      await client.query("commit");
      return {
        item: { ...bundle.project, id: destinationProjectId },
        replaced: Boolean(existing),
      };
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async listProjects(access?: AccessContext): Promise<Project[]> {
    const result = await this.pool.query<ProjectRow>(
      "select * from projects order by updated_at desc, title asc",
    );
    const projects = result.rows.map(projectFromRow);
    if (!access || access.kind === "admin") {
      return projects;
    }
    const allowed = new Set(access.projectIds);
    return projects.filter((project) => allowed.has(project.id));
  }

  async createProject(input: CreateProjectInput): Promise<Project> {
    assertTitle(input.title, "Project");
    const key = normalizeProjectKey(input.key);
    const timestamp = nowIso();
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      await this.assertProjectKeyAvailable(client, key);
      const result = await client.query<ProjectRow>(
        `insert into projects (id, key, title, summary, created_at, updated_at)
         values ($1, $2, $3, $4, $5, $5)
         returning *`,
        [
          randomUUID(),
          key,
          input.title.trim(),
          input.summary?.trim() ?? "",
          timestamp,
        ],
      );
      const project = projectFromRow(result.rows[0]);
      await this.addActivity(
        client,
        "project.created",
        `Created project: ${project.title}`,
        timestamp,
      );
      await client.query("commit");
      return project;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async updateProject(id: string, input: UpdateProjectInput): Promise<Project> {
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const current = await this.requireProject(client, id);
      const key =
        input.key !== undefined ? normalizeProjectKey(input.key) : current.key;
      if (input.key !== undefined) {
        await this.assertProjectKeyAvailable(client, key, current.id);
      }
      const title =
        input.title !== undefined ? input.title.trim() : current.title;
      if (input.title !== undefined) {
        assertTitle(input.title, "Project");
      }
      const summary =
        input.summary !== undefined ? input.summary.trim() : current.summary;
      const timestamp = nowIso();
      const result = await client.query<ProjectRow>(
        `update projects
         set key = $2, title = $3, summary = $4, updated_at = $5
         where id = $1
         returning *`,
        [id, key, title, summary, timestamp],
      );
      const project = projectFromRow(result.rows[0]);
      await this.addActivity(
        client,
        "project.updated",
        `Updated project: ${project.title}`,
        timestamp,
      );
      await client.query("commit");
      return project;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async listIdeas(access: AccessContext, projectId?: string): Promise<Idea[]> {
    if (projectId) {
      const project = await this.requireProject(this.pool, projectId);
      assertCanAccessProject(access, project.id);
      const result = await this.pool.query<IdeaRow>(
        "select * from ideas where project_id = $1 order by updated_at desc, title asc",
        [project.id],
      );
      return this.decorateIdeas(this.pool, result.rows.map(ideaFromRow));
    }

    if (access.kind === "token" && access.projectIds.length === 0) {
      return [];
    }

    const result =
      access.kind === "admin"
        ? await this.pool.query<IdeaRow>(
            "select * from ideas order by updated_at desc, title asc",
          )
        : await this.pool.query<IdeaRow>(
            "select * from ideas where project_id = any($1::text[]) order by updated_at desc, title asc",
            [filterProjectIds(access, access.projectIds)],
          );
    return this.decorateIdeas(this.pool, result.rows.map(ideaFromRow));
  }

  async createIdea(
    access: AccessContext,
    input: CreateIdeaInput,
  ): Promise<Idea> {
    assertTitle(input.title, "Idea");
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const projectId = await this.resolveProjectIdForCreate(
        client,
        defaultProjectIdForCreate(access, input.projectId),
      );
      assertCanAccessProject(access, projectId);
      const timestamp = nowIso();
      const result = await client.query<IdeaRow>(
        `insert into ideas (
           id, project_id, task_number, title, summary, details, status, labels,
           acceptance_criteria, github_issue_url, github_issue_number, repository_local_path,
           repository_remote_url, created_at, updated_at
         )
         values ($1, $2, $3, $4, $5, $6, 'idea', $7, $8, null, null, $9, $10, $11, $11)
         returning *`,
        [
          randomUUID(),
          projectId,
          await this.nextTaskNumber(client, projectId),
          input.title.trim(),
          input.summary?.trim() ?? "",
          input.details?.trim() ?? "",
          JSON.stringify(normalizeList(input.labels)),
          JSON.stringify(normalizeList(input.acceptanceCriteria)),
          normalizeOptionalText(input.repositoryLocalPath) ?? null,
          normalizeOptionalText(input.repositoryRemoteUrl) ?? null,
          timestamp,
        ],
      );
      const idea = ideaFromRow(result.rows[0]);
      if (input.dependsOn?.length) {
        await this.replaceIdeaDependencies(
          client,
          idea,
          input.dependsOn,
          timestamp,
        );
      }
      await this.addActivity(
        client,
        "idea.created",
        `Created idea: ${idea.title}`,
        timestamp,
      );
      const decorated = await this.decorateIdea(client, idea);
      await client.query("commit");
      return decorated;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async updateIdea(
    access: AccessContext,
    id: string,
    input: UpdateIdeaInput,
  ): Promise<Idea> {
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const current = await this.requireIdea(client, id);
      assertCanAccessProject(access, current.projectId);
      const title =
        input.title !== undefined ? input.title.trim() : current.title;
      if (input.title !== undefined) {
        assertTitle(input.title, "Idea");
      }
      const summary =
        input.summary !== undefined ? input.summary.trim() : current.summary;
      const details =
        input.details !== undefined ? input.details.trim() : current.details;
      const labels =
        input.labels !== undefined
          ? normalizeList(input.labels)
          : current.labels;
      const acceptanceCriteria =
        input.acceptanceCriteria !== undefined
          ? normalizeList(input.acceptanceCriteria)
          : current.acceptanceCriteria;
      const repositoryLocalPath =
        input.repositoryLocalPath !== undefined
          ? normalizeOptionalText(input.repositoryLocalPath)
          : current.repositoryLocalPath;
      const repositoryRemoteUrl =
        input.repositoryRemoteUrl !== undefined
          ? normalizeOptionalText(input.repositoryRemoteUrl)
          : current.repositoryRemoteUrl;
      let status = input.status ?? current.status;
      if (
        status === "idea" &&
        (input.details !== undefined ||
          input.acceptanceCriteria !== undefined) &&
        (details || acceptanceCriteria.length > 0)
      ) {
        status = "refining";
      }
      const timestamp = nowIso();
      const result = await client.query<IdeaRow>(
        `update ideas
         set title = $2,
             summary = $3,
             details = $4,
             labels = $5,
             acceptance_criteria = $6,
             status = $7,
             repository_local_path = $8,
             repository_remote_url = $9,
             updated_at = $10
         where id = $1
         returning *`,
        [
          id,
          title,
          summary,
          details,
          JSON.stringify(labels),
          JSON.stringify(acceptanceCriteria),
          status,
          repositoryLocalPath ?? null,
          repositoryRemoteUrl ?? null,
          timestamp,
        ],
      );
      const idea = ideaFromRow(result.rows[0]);
      if (input.dependsOn !== undefined) {
        await this.replaceIdeaDependencies(
          client,
          idea,
          input.dependsOn,
          timestamp,
        );
      }
      await this.syncBoardCardFromIdea(client, idea, timestamp);
      await this.addActivity(
        client,
        "idea.updated",
        `Updated idea: ${idea.title}`,
        timestamp,
      );
      const decorated = await this.decorateIdea(client, idea);
      await client.query("commit");
      return decorated;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async markIdeaReady(access: AccessContext, id: string): Promise<Idea> {
    return this.setIdeaBoardAvailability(access, id, true);
  }

  async setIdeaBoardAvailability(
    access: AccessContext,
    id: string,
    available: boolean,
  ): Promise<Idea> {
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const idea = await this.requireIdea(client, id);
      assertCanAccessProject(access, idea.projectId);
      const timestamp = nowIso();

      if (available) {
        const ready = await this.updateIdeaStatus(
          client,
          idea.id,
          "ready",
          timestamp,
        );
        await this.ensureBoardCard(
          client,
          ready,
          { githubMode: "local" },
          timestamp,
        );
        await this.addActivity(
          client,
          "idea.ready",
          `Marked idea ready: ${ready.title}`,
          timestamp,
        );
        const decorated = await this.decorateIdea(client, ready);
        await client.query("commit");
        return decorated;
      }

      await client.query("delete from board_cards where idea_id = $1", [
        idea.id,
      ]);
      const status =
        idea.details || idea.acceptanceCriteria.length > 0
          ? "refining"
          : "idea";
      const unavailable = await this.updateIdeaStatus(
        client,
        idea.id,
        status,
        timestamp,
      );
      await this.addActivity(
        client,
        "idea.not_ready",
        `Removed idea from board: ${idea.title}`,
        timestamp,
      );
      const decorated = await this.decorateIdea(client, unavailable);
      await client.query("commit");
      return decorated;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async addIdeaDependency(
    access: AccessContext,
    id: string,
    dependsOnId: string,
  ): Promise<Idea> {
    return this.writeIdeaDependencies(access, id, (current) => [
      ...current,
      dependsOnId,
    ]);
  }

  async removeIdeaDependency(
    access: AccessContext,
    id: string,
    dependsOnId: string,
  ): Promise<Idea> {
    return this.writeIdeaDependencies(access, id, (current) =>
      current.filter((item) => item !== dependsOnId),
    );
  }

  async setIdeaDependencies(
    access: AccessContext,
    id: string,
    dependsOnIds: string[],
  ): Promise<Idea> {
    return this.writeIdeaDependencies(access, id, () => dependsOnIds);
  }

  async getWorkOrder(
    access: AccessContext,
    projectId?: string,
  ): Promise<TaskWorkOrder> {
    const [tasks, cards, projects] = await Promise.all([
      this.listIdeas(access, projectId),
      this.listBoardCards(access, projectId),
      this.listProjects(access),
    ]);
    return buildWorkOrder(
      tasks,
      cards,
      new Map(projects.map((project) => [project.id, project.key])),
    );
  }

  private async writeIdeaDependencies(
    access: AccessContext,
    id: string,
    change: (current: string[]) => string[],
  ): Promise<Idea> {
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const idea = await this.requireIdea(client, id);
      assertCanAccessProject(access, idea.projectId);
      const current = await client.query<DependencyRow>(
        "select idea_id, depends_on_idea_id from task_dependencies where idea_id = $1 order by depends_on_idea_id asc",
        [id],
      );
      const timestamp = nowIso();
      const next = normalizeDependencyIds(
        change(current.rows.map((row) => row.depends_on_idea_id)),
      );
      await this.replaceIdeaDependencies(client, idea, next, timestamp);
      const decorated = await this.decorateIdea(client, idea);
      await this.addActivity(
        client,
        "idea.dependencies",
        `Updated dependencies: ${idea.title}`,
        timestamp,
      );
      await client.query("commit");
      return decorated;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async createBoardCardFromIdea(
    access: AccessContext,
    id: string,
    options: CreateBoardCardOptions,
  ): Promise<BoardCard> {
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const idea = await this.requireIdea(client, id);
      assertCanAccessProject(access, idea.projectId);
      if (idea.status !== "ready") {
        throw new Error("Only ready ideas can be moved to the board");
      }
      const card = await this.decorateCard(
        client,
        await this.ensureBoardCard(client, idea, options),
      );
      await client.query("commit");
      return card;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async listBoardCards(
    access: AccessContext,
    projectId?: string,
  ): Promise<BoardCard[]> {
    if (projectId) {
      const project = await this.requireProject(this.pool, projectId);
      assertCanAccessProject(access, project.id);
      const result = await this.pool.query<BoardCardRow>(
        "select * from board_cards where project_id = $1 order by updated_at desc, title asc",
        [project.id],
      );
      return this.decorateCards(this.pool, result.rows.map(boardCardFromRow));
    }

    if (access.kind === "token" && access.projectIds.length === 0) {
      return [];
    }

    const result =
      access.kind === "admin"
        ? await this.pool.query<BoardCardRow>(
            "select * from board_cards order by updated_at desc, title asc",
          )
        : await this.pool.query<BoardCardRow>(
            "select * from board_cards where project_id = any($1::text[]) order by updated_at desc, title asc",
            [access.projectIds],
          );
    return this.decorateCards(this.pool, result.rows.map(boardCardFromRow));
  }

  async getBoardColumns(
    access: AccessContext,
    projectId?: string,
  ): Promise<BoardColumns> {
    const cards = await this.listBoardCards(access, projectId);
    const columns: BoardColumns = {
      ready: [],
      planned: [],
      in_progress: [],
      review: [],
      done: [],
    };
    for (const card of cards) {
      columns[card.column].push(card);
    }
    for (const column of boardColumns) {
      columns[column].sort(sortUpdatedDesc);
    }
    return columns;
  }

  async updateBoardCard(
    access: AccessContext,
    id: string,
    input: UpdateBoardCardInput,
  ): Promise<BoardCard> {
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const current = await this.requireBoardCard(client, id);
      assertCanAccessProject(access, current.projectId);
      const column = input.column ?? current.column;
      const branchName =
        input.branchName !== undefined
          ? normalizeOptionalText(input.branchName)
          : current.branchName;
      const details =
        input.details !== undefined ? input.details.trim() : current.details;
      const repositoryLocalPath =
        input.repositoryLocalPath !== undefined
          ? normalizeOptionalText(input.repositoryLocalPath)
          : current.repositoryLocalPath;
      const repositoryRemoteUrl =
        input.repositoryRemoteUrl !== undefined
          ? normalizeOptionalText(input.repositoryRemoteUrl)
          : current.repositoryRemoteUrl;
      const timestamp = nowIso();
      const result = await client.query<BoardCardRow>(
        `update board_cards
         set column_name = $2,
             branch_name = $3,
             details = $4,
             repository_local_path = $5,
             repository_remote_url = $6,
             updated_at = $7
         where id = $1
         returning *`,
        [
          id,
          column,
          branchName ?? null,
          details,
          repositoryLocalPath ?? null,
          repositoryRemoteUrl ?? null,
          timestamp,
        ],
      );
      const card = await this.decorateCard(
        client,
        boardCardFromRow(result.rows[0]),
      );
      await this.addActivity(
        client,
        "board.updated",
        `Updated board card: ${card.title}`,
        timestamp,
      );
      await client.query("commit");
      return card;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async moveBoardCard(
    access: AccessContext,
    id: string,
    column: BoardColumn,
  ): Promise<BoardCard> {
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const current = await this.requireBoardCard(client, id);
      assertCanAccessProject(access, current.projectId);
      const timestamp = nowIso();
      const result = await client.query<BoardCardRow>(
        `update board_cards
         set column_name = $2, updated_at = $3
         where id = $1
         returning *`,
        [id, column, timestamp],
      );
      const card = await this.decorateCard(
        client,
        boardCardFromRow(result.rows[0]),
      );
      await this.addActivity(
        client,
        "board.moved",
        `Moved card to ${column}: ${card.title}`,
        timestamp,
      );
      await client.query("commit");
      return card;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async setCardReadiness(
    access: AccessContext,
    id: string,
    input: SetCardReadinessInput,
  ): Promise<BoardCard> {
    const existing = await this.pool.query<BoardCardRow>(
      "select * from board_cards where id = $1",
      [id],
    );
    const row = existing.rows[0];
    if (!row) {
      throw new Error(`Board card not found: ${id}`);
    }
    assertCanAccessProject(access, row.project_id);

    if (row.idea_id) {
      await this.setIdeaReadiness(access, row.idea_id, input);
      const reread = await this.pool.query<BoardCardRow>(
        "select * from board_cards where id = $1",
        [id],
      );
      return this.decorateCard(this.pool, boardCardFromRow(reread.rows[0]));
    }

    const { score, reason } = normalizeReadinessInput(input);
    const timestamp = nowIso();
    const result = await this.pool.query<BoardCardRow>(
      `update board_cards
       set readiness_score = $2,
           readiness_reason = $3,
           readiness_evaluated_at = $4
       where id = $1
       returning *`,
      [id, score, reason, timestamp],
    );
    return this.decorateCard(this.pool, boardCardFromRow(result.rows[0]));
  }

  async setIdeaReadiness(
    access: AccessContext,
    id: string,
    input: SetCardReadinessInput,
  ): Promise<Idea> {
    const { score, reason } = normalizeReadinessInput(input);
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const current = await this.requireIdea(client, id);
      assertCanAccessProject(access, current.projectId);
      const timestamp = nowIso();
      const result = await client.query<IdeaRow>(
        `update ideas
         set readiness_score = $2,
             readiness_reason = $3,
             readiness_evaluated_at = $4
         where id = $1
         returning *`,
        [id, score, reason, timestamp],
      );
      await client.query(
        `update board_cards
         set readiness_score = $2,
             readiness_reason = $3,
             readiness_evaluated_at = $4
         where idea_id = $1`,
        [id, score, reason, timestamp],
      );
      await client.query(
        `insert into idea_readiness_events (id, idea_id, score, reason, created_at)
         values ($1, $2, $3, $4, $5)`,
        [randomUUID(), id, score, reason, timestamp],
      );
      const idea = await this.decorateIdea(client, ideaFromRow(result.rows[0]));
      await this.addActivity(
        client,
        "idea.readiness",
        `Set readiness ${score}/10: ${idea.title}`,
        timestamp,
      );
      await client.query("commit");
      return idea;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async listIdeaReadiness(
    access: AccessContext,
    id: string,
  ): Promise<ReadinessEvent[]> {
    const idea = await this.requireIdea(this.pool, id);
    assertCanAccessProject(access, idea.projectId);
    const result = await this.pool.query<ReadinessEventRow>(
      "select * from idea_readiness_events where idea_id = $1 order by created_at desc",
      [id],
    );
    return result.rows.map(readinessEventFromRow);
  }

  async listDocuments(
    access: AccessContext,
    projectId?: string,
  ): Promise<PlanDocument[]> {
    if (projectId) {
      const project = await this.requireProject(this.pool, projectId);
      assertCanAccessProject(access, project.id);
      const result = await this.pool.query<DocumentRow>(
        "select * from documents where project_id = $1 order by updated_at desc, title asc",
        [project.id],
      );
      return result.rows.map(documentFromRow);
    }

    if (access.kind === "token" && access.projectIds.length === 0) {
      return [];
    }

    const result =
      access.kind === "admin"
        ? await this.pool.query<DocumentRow>(
            "select * from documents order by updated_at desc, title asc",
          )
        : await this.pool.query<DocumentRow>(
            "select * from documents where project_id = any($1::text[]) order by updated_at desc, title asc",
            [access.projectIds],
          );
    return result.rows.map(documentFromRow);
  }

  async createDocument(
    access: AccessContext,
    input: CreateDocumentInput,
  ): Promise<PlanDocument> {
    assertTitle(input.title, "Document");
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const projectId = await this.resolveProjectIdForCreate(
        client,
        defaultProjectIdForCreate(access, input.projectId),
      );
      assertCanAccessProject(access, projectId);
      const timestamp = nowIso();
      const result = await client.query<DocumentRow>(
        `insert into documents (
           id, project_id, title, kind, content, linked_idea_ids, linked_card_ids, created_at, updated_at
         )
         values ($1, $2, $3, $4, $5, $6, $7, $8, $8)
         returning *`,
        [
          randomUUID(),
          projectId,
          input.title.trim(),
          input.kind ?? "execution_plan",
          input.content?.trim() ?? "",
          JSON.stringify(normalizeList(input.linkedIdeaIds)),
          JSON.stringify(normalizeList(input.linkedCardIds)),
          timestamp,
        ],
      );
      const document = documentFromRow(result.rows[0]);
      await this.addActivity(
        client,
        "document.created",
        `Created document: ${document.title}`,
        timestamp,
      );
      await client.query("commit");
      return document;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async updateDocument(
    access: AccessContext,
    id: string,
    input: UpdateDocumentInput,
  ): Promise<PlanDocument> {
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const current = await this.requireDocument(client, id);
      assertCanAccessProject(access, current.projectId);
      const title =
        input.title !== undefined ? input.title.trim() : current.title;
      if (input.title !== undefined) {
        assertTitle(input.title, "Document");
      }
      const timestamp = nowIso();
      const result = await client.query<DocumentRow>(
        `update documents
         set title = $2,
             kind = $3,
             content = $4,
             linked_idea_ids = $5,
             linked_card_ids = $6,
             updated_at = $7
         where id = $1
         returning *`,
        [
          id,
          title,
          input.kind ?? current.kind,
          input.content ?? current.content,
          JSON.stringify(
            input.linkedIdeaIds !== undefined
              ? normalizeList(input.linkedIdeaIds)
              : current.linkedIdeaIds,
          ),
          JSON.stringify(
            input.linkedCardIds !== undefined
              ? normalizeList(input.linkedCardIds)
              : current.linkedCardIds,
          ),
          timestamp,
        ],
      );
      const document = documentFromRow(result.rows[0]);
      await this.addActivity(
        client,
        "document.updated",
        `Updated document: ${document.title}`,
        timestamp,
      );
      await client.query("commit");
      return document;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async listActivity(access: AccessContext): Promise<ActivityEvent[]> {
    if (access.kind === "token") {
      return [];
    }
    const result = await this.pool.query<ActivityRow>(
      "select * from activity_events order by created_at desc",
    );
    return result.rows.map(activityFromRow);
  }

  async listApiTokens(): Promise<ApiTokenSummary[]> {
    const result = await this.pool.query<ApiTokenRow>(
      "select id, name, created_at, last_used_at, revoked_at from api_tokens order by created_at desc",
    );
    return Promise.all(result.rows.map((row) => this.apiTokenSummary(row)));
  }

  async createApiToken(input: CreateApiTokenInput): Promise<CreatedApiToken> {
    if (!input.name.trim()) {
      throw new Error("Token name is required");
    }
    if (input.projectIds.length === 0) {
      throw new Error("At least one project is required");
    }
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      await this.requireProjects(client, input.projectIds);
      const token = createRawApiToken();
      const timestamp = nowIso();
      const created = await client.query<ApiTokenRow>(
        `insert into api_tokens (id, name, token_hash, created_at)
         values ($1, $2, $3, $4)
         returning id, name, created_at, last_used_at, revoked_at`,
        [randomUUID(), input.name.trim(), hashApiToken(token), timestamp],
      );
      for (const projectId of normalizeList(input.projectIds)) {
        await client.query(
          "insert into api_token_projects (token_id, project_id) values ($1, $2)",
          [created.rows[0].id, projectId],
        );
      }
      const item = await this.apiTokenSummary(created.rows[0], client);
      await client.query("commit");
      return {
        item,
        token,
      };
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async updateApiToken(
    id: string,
    input: UpdateApiTokenInput,
  ): Promise<ApiTokenSummary> {
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const current = await this.requireApiToken(client, id);
      const name = input.name !== undefined ? input.name.trim() : current.name;
      if (!name) {
        throw new Error("Token name is required");
      }
      const updated = await client.query<ApiTokenRow>(
        `update api_tokens
         set name = $2
         where id = $1
         returning id, name, created_at, last_used_at, revoked_at`,
        [id, name],
      );
      if (input.projectIds !== undefined) {
        if (input.projectIds.length === 0) {
          throw new Error("At least one project is required");
        }
        await this.requireProjects(client, input.projectIds);
        await client.query(
          "delete from api_token_projects where token_id = $1",
          [id],
        );
        for (const projectId of normalizeList(input.projectIds)) {
          await client.query(
            "insert into api_token_projects (token_id, project_id) values ($1, $2)",
            [id, projectId],
          );
        }
      }
      const item = await this.apiTokenSummary(updated.rows[0], client);
      await client.query("commit");
      return item;
    } catch (error) {
      await client.query("rollback");
      throw error;
    } finally {
      client.release();
    }
  }

  async revokeApiToken(id: string): Promise<ApiTokenSummary> {
    const result = await this.pool.query<ApiTokenRow>(
      `update api_tokens
       set revoked_at = coalesce(revoked_at, $2)
       where id = $1
       returning id, name, created_at, last_used_at, revoked_at`,
      [id, nowIso()],
    );
    const row = result.rows[0];
    if (!row) {
      throw new Error(`API token not found: ${id}`);
    }
    return this.apiTokenSummary(row);
  }

  async authenticateApiToken(
    token: string,
  ): Promise<AuthenticatedToken | null> {
    const result = await this.pool.query<{ id: string }>(
      `update api_tokens
       set last_used_at = $2
       where token_hash = $1 and revoked_at is null
       returning id`,
      [hashApiToken(token), nowIso()],
    );
    const row = result.rows[0];
    if (!row) {
      return null;
    }
    const projectIds = await this.apiTokenProjectIds(this.pool, row.id);
    return { tokenId: row.id, projectIds };
  }

  /**
   * Loads the dependency edges touching the given tasks plus the completion
   * state of every blocker, so ideas and cards can carry dependsOn/blockedBy.
   */
  private async dependencyContext(
    queryable: Pick<Pool | PoolClient, "query">,
    ideaIds: string[],
  ): Promise<{
    dependsOn: Map<string, string[]>;
    blocks: Map<string, string[]>;
    complete: Map<string, boolean>;
  }> {
    const dependsOn = new Map<string, string[]>();
    const blocks = new Map<string, string[]>();
    const complete = new Map<string, boolean>();
    const ids = [...new Set(ideaIds)];
    if (ids.length === 0) {
      return { dependsOn, blocks, complete };
    }

    const edges = await queryable.query<DependencyRow>(
      `select idea_id, depends_on_idea_id
       from task_dependencies
       where idea_id = any($1::text[]) or depends_on_idea_id = any($1::text[])
       order by idea_id asc, depends_on_idea_id asc`,
      [ids],
    );
    for (const row of edges.rows) {
      dependsOn.set(row.idea_id, [
        ...(dependsOn.get(row.idea_id) ?? []),
        row.depends_on_idea_id,
      ]);
      blocks.set(row.depends_on_idea_id, [
        ...(blocks.get(row.depends_on_idea_id) ?? []),
        row.idea_id,
      ]);
    }

    const blockerIds = [
      ...new Set(edges.rows.map((row) => row.depends_on_idea_id)),
    ];
    if (blockerIds.length > 0) {
      const states = await queryable.query<TaskStateRow>(
        `select ideas.id, ideas.status, board_cards.column_name
         from ideas
         left join board_cards on board_cards.idea_id = ideas.id
         where ideas.id = any($1::text[])`,
        [blockerIds],
      );
      for (const row of states.rows) {
        const resolved =
          isTaskComplete({
            status: row.status,
            column: row.column_name ?? undefined,
          }) ||
          (complete.get(row.id) ?? false);
        complete.set(row.id, resolved);
      }
    }

    return { dependsOn, blocks, complete };
  }

  private async decorateIdeas(
    queryable: Pick<Pool | PoolClient, "query">,
    ideas: Idea[],
  ): Promise<Idea[]> {
    if (ideas.length === 0) {
      return ideas;
    }
    const context = await this.dependencyContext(
      queryable,
      ideas.map((idea) => idea.id),
    );
    return ideas.map((idea) => {
      const dependsOn = context.dependsOn.get(idea.id) ?? [];
      return {
        ...idea,
        dependsOn,
        blocks: context.blocks.get(idea.id) ?? [],
        blockedBy: dependsOn.filter((id) => !context.complete.get(id)),
      };
    });
  }

  private async decorateIdea(
    queryable: Pick<Pool | PoolClient, "query">,
    idea: Idea,
  ): Promise<Idea> {
    const [decorated] = await this.decorateIdeas(queryable, [idea]);
    return decorated ?? idea;
  }

  private async decorateCards(
    queryable: Pick<Pool | PoolClient, "query">,
    cards: BoardCard[],
  ): Promise<BoardCard[]> {
    const ideaIds = cards
      .map((card) => card.ideaId)
      .filter((id): id is string => Boolean(id));
    if (ideaIds.length === 0) {
      return cards;
    }
    const context = await this.dependencyContext(queryable, ideaIds);
    return cards.map((card) => {
      const dependsOn = card.ideaId
        ? (context.dependsOn.get(card.ideaId) ?? [])
        : [];
      return {
        ...card,
        dependsOn,
        blockedBy: dependsOn.filter((id) => !context.complete.get(id)),
      };
    });
  }

  private async decorateCard(
    queryable: Pick<Pool | PoolClient, "query">,
    card: BoardCard,
  ): Promise<BoardCard> {
    const [decorated] = await this.decorateCards(queryable, [card]);
    return decorated ?? card;
  }

  /**
   * Replaces a task's blockers inside an open transaction. Blockers must live in
   * the same project, and the resulting graph must stay acyclic so the work
   * order can always be computed.
   */
  private async replaceIdeaDependencies(
    client: PoolClient,
    idea: Idea,
    dependsOnIds: string[],
    timestamp: string,
  ) {
    const requested = normalizeDependencyIds(dependsOnIds);
    if (requested.includes(idea.id)) {
      throw new Error("A task cannot depend on itself");
    }

    if (requested.length > 0) {
      const blockers = await client.query<{ id: string; project_id: string }>(
        "select id, project_id from ideas where id = any($1::text[])",
        [requested],
      );
      const byId = new Map(blockers.rows.map((row) => [row.id, row]));
      for (const id of requested) {
        const blocker = byId.get(id);
        if (!blocker) {
          throw new Error(`Task not found: ${id}`);
        }
        if (blocker.project_id !== idea.projectId) {
          throw new Error(
            `Dependencies must stay in the same project: ${id} belongs to another project`,
          );
        }
      }
    }

    await this.assertAcyclicDependencies(client, idea, requested);

    await client.query("delete from task_dependencies where idea_id = $1", [
      idea.id,
    ]);
    for (const dependsOnId of requested) {
      await client.query(
        `insert into task_dependencies (idea_id, depends_on_idea_id, created_at)
         values ($1, $2, $3)
         on conflict do nothing`,
        [idea.id, dependsOnId, timestamp],
      );
    }
  }

  private async assertAcyclicDependencies(
    client: PoolClient,
    idea: Idea,
    dependsOnIds: string[],
  ) {
    const existing = await client.query<DependencyRow>(
      `select task_dependencies.idea_id, task_dependencies.depends_on_idea_id
       from task_dependencies
       inner join ideas on ideas.id = task_dependencies.idea_id
       where ideas.project_id = $1 and task_dependencies.idea_id <> $2`,
      [idea.projectId, idea.id],
    );
    const edges = new Map<string, string[]>();
    for (const row of existing.rows) {
      edges.set(row.idea_id, [
        ...(edges.get(row.idea_id) ?? []),
        row.depends_on_idea_id,
      ]);
    }
    if (dependsOnIds.length > 0) {
      edges.set(idea.id, dependsOnIds);
    }

    const cyclic = findCyclicTaskIds(edges);
    if (cyclic.length > 0) {
      throw new Error(
        `Dependency cycle detected between tasks: ${cyclic.sort().join(", ")}`,
      );
    }
  }

  private async requireProject(
    queryable: Pick<Pool | PoolClient, "query">,
    id: string,
  ): Promise<Project> {
    const result = await queryable.query<ProjectRow>(
      "select * from projects where id = $1",
      [id],
    );
    const row = result.rows[0];
    if (!row) {
      throw new Error(`Project not found: ${id}`);
    }
    return projectFromRow(row);
  }

  private async requireIdea(
    queryable: Pick<Pool | PoolClient, "query">,
    id: string,
  ): Promise<Idea> {
    const result = await queryable.query<IdeaRow>(
      "select * from ideas where id = $1",
      [id],
    );
    const row = result.rows[0];
    if (!row) {
      throw new Error(`Idea not found: ${id}`);
    }
    return ideaFromRow(row);
  }

  private async requireBoardCard(
    queryable: Pick<Pool | PoolClient, "query">,
    id: string,
  ): Promise<BoardCard> {
    const result = await queryable.query<BoardCardRow>(
      "select * from board_cards where id = $1",
      [id],
    );
    const row = result.rows[0];
    if (!row) {
      throw new Error(`Board card not found: ${id}`);
    }
    return boardCardFromRow(row);
  }

  private async requireDocument(
    queryable: Pick<Pool | PoolClient, "query">,
    id: string,
  ): Promise<PlanDocument> {
    const result = await queryable.query<DocumentRow>(
      "select * from documents where id = $1",
      [id],
    );
    const row = result.rows[0];
    if (!row) {
      throw new Error(`Document not found: ${id}`);
    }
    return documentFromRow(row);
  }

  private async requireApiToken(
    queryable: Pick<Pool | PoolClient, "query">,
    id: string,
  ): Promise<ApiTokenRow> {
    const result = await queryable.query<ApiTokenRow>(
      "select id, name, created_at, last_used_at, revoked_at from api_tokens where id = $1",
      [id],
    );
    const row = result.rows[0];
    if (!row) {
      throw new Error(`API token not found: ${id}`);
    }
    return row;
  }

  private async requireProjects(
    queryable: Pick<Pool | PoolClient, "query">,
    projectIds: string[],
  ) {
    const uniqueIds = normalizeList(projectIds);
    const result = await queryable.query<{ id: string }>(
      "select id from projects where id = any($1::text[])",
      [uniqueIds],
    );
    const found = new Set(result.rows.map((row) => row.id));
    const missing = uniqueIds.find((id) => !found.has(id));
    if (missing) {
      throw new Error(`Project not found: ${missing}`);
    }
  }

  private async resolveProjectIdForCreate(
    client: PoolClient,
    requestedProjectId: string | undefined,
  ): Promise<string> {
    if (requestedProjectId !== undefined) {
      return (await this.requireProject(client, requestedProjectId)).id;
    }

    const existing = await client.query<ProjectRow>(
      "select * from projects order by created_at asc, id asc limit 1",
    );
    if (existing.rows[0]) {
      return existing.rows[0].id;
    }

    const timestamp = nowIso();
    const key = nextAvailableProjectKey([], "GEN");
    const created = await client.query<ProjectRow>(
      `insert into projects (id, key, title, summary, created_at, updated_at)
       values ($1, $2, 'General', $3, $4, $4)
       returning *`,
      [randomUUID(), key, defaultProjectSummary, timestamp],
    );
    const project = projectFromRow(created.rows[0]);
    await this.addActivity(
      client,
      "project.created",
      `Created project: ${project.title}`,
      timestamp,
    );
    return project.id;
  }

  private async assertProjectKeyAvailable(
    queryable: Pick<Pool | PoolClient, "query">,
    key: string,
    currentProjectId?: string,
  ) {
    const result = await queryable.query<ProjectRow>(
      "select * from projects where key = $1 and ($2::text is null or id <> $2) limit 1",
      [key, currentProjectId ?? null],
    );
    if (result.rows[0]) {
      throw new Error(`Project ID ${key} is already used`);
    }
  }

  private async nextTaskNumber(
    queryable: Pick<Pool | PoolClient, "query">,
    projectId: string,
  ): Promise<number> {
    const result = await queryable.query<{ next_number: number }>(
      "select coalesce(max(task_number), 0) + 1 as next_number from ideas where project_id = $1",
      [projectId],
    );
    return Number(result.rows[0]?.next_number ?? 1);
  }

  private async updateIdeaStatus(
    queryable: Pick<Pool | PoolClient, "query">,
    id: string,
    status: Idea["status"],
    timestamp = nowIso(),
  ): Promise<Idea> {
    const result = await queryable.query<IdeaRow>(
      "update ideas set status = $2, updated_at = $3 where id = $1 returning *",
      [id, status, timestamp],
    );
    return ideaFromRow(result.rows[0]);
  }

  private async ensureBoardCard(
    queryable: Pick<Pool | PoolClient, "query">,
    idea: Idea,
    options: CreateBoardCardOptions,
    timestamp = nowIso(),
  ): Promise<BoardCard> {
    const existing = await queryable.query<BoardCardRow>(
      "select * from board_cards where idea_id = $1 limit 1",
      [idea.id],
    );
    if (existing.rows[0]) {
      const githubIssueUrl =
        options.githubMode === "github"
          ? options.githubIssueUrl
          : existing.rows[0].github_issue_url;
      const githubIssueNumber =
        options.githubMode === "github"
          ? options.githubIssueNumber
          : existing.rows[0].github_issue_number;
      const updated = await queryable.query<BoardCardRow>(
        `update board_cards
         set title = $2,
             details = $3,
             labels = $4,
             repository_local_path = $5,
             repository_remote_url = $6,
             github_issue_url = $7,
             github_issue_number = $8,
             updated_at = $9
         where id = $1
         returning *`,
        [
          existing.rows[0].id,
          idea.title,
          boardCardDetailsForIdea(idea),
          JSON.stringify(idea.labels),
          idea.repositoryLocalPath ?? null,
          idea.repositoryRemoteUrl ?? null,
          githubIssueUrl,
          githubIssueNumber,
          timestamp,
        ],
      );
      if (options.githubMode === "github") {
        await this.applyIdeaGitHubIssue(queryable, idea.id, options, timestamp);
      }
      return boardCardFromRow(updated.rows[0]);
    }

    const created = await queryable.query<BoardCardRow>(
      `insert into board_cards (
         id, project_id, idea_id, title, details, column_name, branch_name, github_issue_url,
         github_issue_number, repository_local_path, repository_remote_url, labels,
         readiness_score, readiness_reason, readiness_evaluated_at, created_at, updated_at
       )
       values ($1, $2, $3, $4, $5, 'ready', null, $6, $7, $8, $9, $10, $12, $13, $14, $11, $11)
       returning *`,
      [
        randomUUID(),
        idea.projectId,
        idea.id,
        idea.title,
        boardCardDetailsForIdea(idea),
        options.githubMode === "github" ? options.githubIssueUrl : null,
        options.githubMode === "github" ? options.githubIssueNumber : null,
        idea.repositoryLocalPath ?? null,
        idea.repositoryRemoteUrl ?? null,
        JSON.stringify(idea.labels),
        timestamp,
        idea.readinessScore ?? null,
        idea.readinessReason ?? null,
        idea.readinessEvaluatedAt ?? null,
      ],
    );
    if (options.githubMode === "github") {
      await this.applyIdeaGitHubIssue(queryable, idea.id, options, timestamp);
    }
    const card = boardCardFromRow(created.rows[0]);
    await this.addActivity(
      queryable,
      "board.created",
      `Created board card: ${card.title}`,
      timestamp,
    );
    return card;
  }

  private async syncBoardCardFromIdea(
    queryable: Pick<Pool | PoolClient, "query">,
    idea: Idea,
    timestamp = nowIso(),
  ) {
    await queryable.query(
      `update board_cards
       set title = $2,
           details = $3,
           labels = $4,
           repository_local_path = $5,
           repository_remote_url = $6,
           updated_at = $7
       where idea_id = $1`,
      [
        idea.id,
        idea.title,
        boardCardDetailsForIdea(idea),
        JSON.stringify(idea.labels),
        idea.repositoryLocalPath ?? null,
        idea.repositoryRemoteUrl ?? null,
        timestamp,
      ],
    );
  }

  private async applyIdeaGitHubIssue(
    queryable: Pick<Pool | PoolClient, "query">,
    ideaId: string,
    options: CreateBoardCardOptions,
    timestamp = nowIso(),
  ) {
    if (options.githubMode !== "github") {
      return;
    }
    await queryable.query(
      `update ideas
       set github_issue_url = $2,
           github_issue_number = $3,
           updated_at = $4
       where id = $1`,
      [ideaId, options.githubIssueUrl, options.githubIssueNumber, timestamp],
    );
  }

  private async assertImportedIdsAvailable(
    client: PoolClient,
    bundle: ProjectBundle,
    destinationProjectId: string,
  ) {
    const assertTableIdsAvailable = async (
      entity: string,
      table: "ideas" | "board_cards" | "documents",
      ids: string[],
    ) => {
      if (ids.length === 0) {
        return;
      }
      const collision = await client.query<{ id: string }>(
        `select id from ${table}
         where id = any($1::text[]) and project_id <> $2
         limit 1`,
        [ids, destinationProjectId],
      );
      if (collision.rows[0]) {
        throw new Error(
          `${entity} ID is already used by another project: ${collision.rows[0].id}`,
        );
      }
    };

    await assertTableIdsAvailable(
      "Idea",
      "ideas",
      bundle.ideas.map((idea) => idea.id),
    );
    await assertTableIdsAvailable(
      "Board card",
      "board_cards",
      bundle.boardCards.map((card) => card.id),
    );
    await assertTableIdsAvailable(
      "Document",
      "documents",
      bundle.documents.map((document) => document.id),
    );

    const readinessIds = bundle.readinessEvents.map((event) => event.id);
    if (readinessIds.length > 0) {
      const collision = await client.query<{ id: string }>(
        `select events.id
         from idea_readiness_events events
         join ideas on ideas.id = events.idea_id
         where events.id = any($1::text[]) and ideas.project_id <> $2
         limit 1`,
        [readinessIds, destinationProjectId],
      );
      if (collision.rows[0]) {
        throw new Error(
          `Readiness event ID is already used by another project: ${collision.rows[0].id}`,
        );
      }
    }
  }

  private async insertImportedProject(
    client: PoolClient,
    bundle: ProjectBundle,
    destinationProjectId: string,
  ) {
    await client.query(
      `insert into projects (id, key, title, summary, created_at, updated_at)
       values ($1, $2, $3, $4, $5, $6)`,
      [
        destinationProjectId,
        bundle.project.key,
        bundle.project.title,
        bundle.project.summary,
        bundle.project.createdAt,
        bundle.project.updatedAt,
      ],
    );
    await this.insertImportedChildren(client, bundle, destinationProjectId);
  }

  private async insertImportedChildren(
    client: PoolClient,
    bundle: ProjectBundle,
    destinationProjectId: string,
  ) {
    for (const idea of bundle.ideas) {
      await client.query(
        `insert into ideas (
           id, project_id, task_number, title, summary, details, status, labels,
           acceptance_criteria, github_issue_url, github_issue_number, repository_local_path,
           repository_remote_url, readiness_score, readiness_reason, readiness_evaluated_at,
           created_at, updated_at
         )
         values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)`,
        [
          idea.id,
          destinationProjectId,
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
          idea.readinessScore ?? null,
          idea.readinessReason ?? null,
          idea.readinessEvaluatedAt ?? null,
          idea.createdAt,
          idea.updatedAt,
        ],
      );
    }

    for (const idea of bundle.ideas) {
      for (const dependsOnId of new Set(idea.dependsOn)) {
        await client.query(
          `insert into task_dependencies (idea_id, depends_on_idea_id, created_at)
           values ($1, $2, $3)`,
          [idea.id, dependsOnId, idea.updatedAt],
        );
      }
    }

    for (const card of bundle.boardCards) {
      await client.query(
        `insert into board_cards (
           id, project_id, idea_id, title, details, column_name, branch_name, github_issue_url,
           github_issue_number, repository_local_path, repository_remote_url, labels,
           readiness_score, readiness_reason, readiness_evaluated_at, created_at, updated_at
         )
         values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)`,
        [
          card.id,
          destinationProjectId,
          card.ideaId ?? null,
          card.title,
          card.details,
          card.column,
          card.branchName ?? null,
          card.githubIssueUrl ?? null,
          card.githubIssueNumber ?? null,
          card.repositoryLocalPath ?? null,
          card.repositoryRemoteUrl ?? null,
          JSON.stringify(card.labels),
          card.readinessScore ?? null,
          card.readinessReason ?? null,
          card.readinessEvaluatedAt ?? null,
          card.createdAt,
          card.updatedAt,
        ],
      );
    }

    for (const event of bundle.readinessEvents) {
      await client.query(
        `insert into idea_readiness_events (id, idea_id, score, reason, created_at)
         values ($1, $2, $3, $4, $5)`,
        [event.id, event.ideaId, event.score, event.reason, event.createdAt],
      );
    }

    for (const document of bundle.documents) {
      await client.query(
        `insert into documents (
           id, project_id, title, kind, content, linked_idea_ids, linked_card_ids, created_at, updated_at
         )
         values ($1, $2, $3, $4, $5, $6, $7, $8, $9)`,
        [
          document.id,
          destinationProjectId,
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
  }

  private async addActivity(
    queryable: Pick<Pool | PoolClient, "query">,
    type: string,
    message: string,
    createdAt = nowIso(),
  ) {
    await queryable.query(
      "insert into activity_events (id, type, message, created_at) values ($1, $2, $3, $4)",
      [randomUUID(), type, message, createdAt],
    );
    await queryable.query(
      `delete from activity_events
       where id not in (
         select id from activity_events
         order by created_at desc
         limit 100
      )`,
    );
  }

  private async apiTokenSummary(
    row: ApiTokenRow,
    queryable: Pick<Pool | PoolClient, "query"> = this.pool,
  ): Promise<ApiTokenSummary> {
    const projects = await queryable.query<
      Pick<ProjectRow, "id" | "key" | "title">
    >(
      `select projects.id, projects.key, projects.title
       from projects
       inner join api_token_projects on api_token_projects.project_id = projects.id
       where api_token_projects.token_id = $1
       order by projects.key asc, projects.title asc`,
      [row.id],
    );
    return {
      id: row.id,
      name: row.name,
      projects: projects.rows,
      createdAt: rowTimestamp(row.created_at),
      lastUsedAt: row.last_used_at ? rowTimestamp(row.last_used_at) : undefined,
      revokedAt: row.revoked_at ? rowTimestamp(row.revoked_at) : undefined,
    };
  }

  private async apiTokenProjectIds(
    queryable: Pick<Pool | PoolClient, "query">,
    tokenId: string,
  ): Promise<string[]> {
    const result = await queryable.query<{ project_id: string }>(
      "select project_id from api_token_projects where token_id = $1 order by project_id asc",
      [tokenId],
    );
    return result.rows.map((row) => row.project_id);
  }
}

const rowTimestamp = (value: Date | string): string =>
  value instanceof Date ? value.toISOString() : new Date(value).toISOString();

const jsonStringArray = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];

const boardCardDetailsForIdea = (
  idea: Pick<Idea, "details" | "summary">,
): string => idea.details || idea.summary;

const projectFromRow = (row: ProjectRow): Project => ({
  id: row.id,
  key: row.key,
  title: row.title,
  summary: row.summary,
  createdAt: rowTimestamp(row.created_at),
  updatedAt: rowTimestamp(row.updated_at),
});

const ideaFromRow = (row: IdeaRow): Idea => ({
  id: row.id,
  projectId: row.project_id,
  taskNumber: row.task_number,
  title: row.title,
  summary: row.summary,
  details: row.details,
  status: row.status,
  labels: jsonStringArray(row.labels),
  acceptanceCriteria: jsonStringArray(row.acceptance_criteria),
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  githubIssueUrl: row.github_issue_url ?? undefined,
  githubIssueNumber: row.github_issue_number ?? undefined,
  repositoryLocalPath: row.repository_local_path ?? undefined,
  repositoryRemoteUrl: row.repository_remote_url ?? undefined,
  readinessScore: row.readiness_score ?? undefined,
  readinessReason: row.readiness_reason ?? undefined,
  readinessEvaluatedAt: row.readiness_evaluated_at
    ? rowTimestamp(row.readiness_evaluated_at)
    : undefined,
  createdAt: rowTimestamp(row.created_at),
  updatedAt: rowTimestamp(row.updated_at),
});

const readinessEventFromRow = (row: ReadinessEventRow): ReadinessEvent => ({
  id: row.id,
  ideaId: row.idea_id,
  score: row.score,
  reason: row.reason,
  createdAt: rowTimestamp(row.created_at),
});

const boardCardFromRow = (row: BoardCardRow): BoardCard => ({
  id: row.id,
  projectId: row.project_id,
  title: row.title,
  details: row.details,
  column: row.column_name,
  branchName: row.branch_name ?? undefined,
  ideaId: row.idea_id ?? undefined,
  githubIssueUrl: row.github_issue_url ?? undefined,
  githubIssueNumber: row.github_issue_number ?? undefined,
  repositoryLocalPath: row.repository_local_path ?? undefined,
  repositoryRemoteUrl: row.repository_remote_url ?? undefined,
  labels: jsonStringArray(row.labels),
  dependsOn: [],
  blockedBy: [],
  readinessScore: row.readiness_score ?? undefined,
  readinessReason: row.readiness_reason ?? undefined,
  readinessEvaluatedAt: row.readiness_evaluated_at
    ? rowTimestamp(row.readiness_evaluated_at)
    : undefined,
  createdAt: rowTimestamp(row.created_at),
  updatedAt: rowTimestamp(row.updated_at),
});

const documentFromRow = (row: DocumentRow): PlanDocument => ({
  id: row.id,
  projectId: row.project_id,
  title: row.title,
  kind: row.kind,
  content: row.content,
  linkedIdeaIds: jsonStringArray(row.linked_idea_ids),
  linkedCardIds: jsonStringArray(row.linked_card_ids),
  createdAt: rowTimestamp(row.created_at),
  updatedAt: rowTimestamp(row.updated_at),
});

const activityFromRow = (row: ActivityRow): ActivityEvent => ({
  id: row.id,
  type: row.type,
  message: row.message,
  createdAt: rowTimestamp(row.created_at),
});

export class BoardStore {
  private data: BoardData;

  constructor(private readonly filePath: string) {
    const { data, migrated } = this.load();
    this.data = data;
    if (migrated) {
      this.save();
    }
  }

  getState(): BoardData {
    return clone(this.data);
  }

  listProjects(): Project[] {
    return clone(this.data.projects).sort(sortUpdatedDesc);
  }

  createProject(input: CreateProjectInput): Project {
    assertTitle(input.title, "Project");
    const key = normalizeProjectKey(input.key);
    this.assertProjectKeyAvailable(key);
    const timestamp = nowIso();
    const project: Project = {
      id: randomUUID(),
      key,
      title: input.title.trim(),
      summary: input.summary?.trim() ?? "",
      createdAt: timestamp,
      updatedAt: timestamp,
    };

    this.data.projects.push(project);
    this.addActivity(
      "project.created",
      `Created project: ${project.title}`,
      timestamp,
    );
    this.save();
    return clone(project);
  }

  updateProject(id: string, input: UpdateProjectInput): Project {
    const project = this.requireProject(id);
    if (input.key !== undefined) {
      const key = normalizeProjectKey(input.key);
      this.assertProjectKeyAvailable(key, project.id);
      project.key = key;
    }
    if (input.title !== undefined) {
      assertTitle(input.title, "Project");
      project.title = input.title.trim();
    }
    if (input.summary !== undefined) {
      project.summary = input.summary.trim();
    }

    project.updatedAt = nowIso();
    this.addActivity("project.updated", `Updated project: ${project.title}`);
    this.save();
    return clone(project);
  }

  listIdeas(projectId?: string): Idea[] {
    const filtered = projectId
      ? this.data.ideas.filter(
          (idea) => idea.projectId === this.requireProject(projectId).id,
        )
      : this.data.ideas;
    return clone(filtered).sort(sortUpdatedDesc);
  }

  createIdea(input: CreateIdeaInput): Idea {
    assertTitle(input.title, "Idea");
    const projectId = this.resolveProjectId(input.projectId);
    const timestamp = nowIso();
    const idea: Idea = {
      id: randomUUID(),
      projectId,
      taskNumber: this.nextTaskNumber(projectId),
      title: input.title.trim(),
      summary: input.summary?.trim() ?? "",
      details: input.details?.trim() ?? "",
      status: "idea",
      labels: normalizeList(input.labels),
      acceptanceCriteria: normalizeList(input.acceptanceCriteria),
      dependsOn: normalizeDependencyIds(input.dependsOn),
      blocks: [],
      blockedBy: [],
      repositoryLocalPath: normalizeOptionalText(input.repositoryLocalPath),
      repositoryRemoteUrl: normalizeOptionalText(input.repositoryRemoteUrl),
      createdAt: timestamp,
      updatedAt: timestamp,
    };

    this.data.ideas.push(idea);
    this.addActivity("idea.created", `Created idea: ${idea.title}`, timestamp);
    this.save();
    return clone(idea);
  }

  updateIdea(id: string, input: UpdateIdeaInput): Idea {
    const idea = this.requireIdea(id);
    if (input.title !== undefined) {
      assertTitle(input.title, "Idea");
      idea.title = input.title.trim();
    }
    if (input.summary !== undefined) {
      idea.summary = input.summary.trim();
    }
    if (input.details !== undefined) {
      idea.details = input.details.trim();
      if (idea.status === "idea") {
        idea.status = "refining";
      }
    }
    if (input.labels !== undefined) {
      idea.labels = normalizeList(input.labels);
    }
    if (input.acceptanceCriteria !== undefined) {
      idea.acceptanceCriteria = normalizeList(input.acceptanceCriteria);
      if (idea.status === "idea") {
        idea.status = "refining";
      }
    }
    if (input.dependsOn !== undefined) {
      idea.dependsOn = normalizeDependencyIds(input.dependsOn);
    }
    if (input.repositoryLocalPath !== undefined) {
      idea.repositoryLocalPath = normalizeOptionalText(
        input.repositoryLocalPath,
      );
    }
    if (input.repositoryRemoteUrl !== undefined) {
      idea.repositoryRemoteUrl = normalizeOptionalText(
        input.repositoryRemoteUrl,
      );
    }
    if (input.status !== undefined) {
      idea.status = input.status;
    }

    idea.updatedAt = nowIso();
    this.syncBoardCardFromIdea(idea, idea.updatedAt);
    this.addActivity("idea.updated", `Updated idea: ${idea.title}`);
    this.save();
    return clone(idea);
  }

  markIdeaReady(id: string): Idea {
    return this.setIdeaBoardAvailability(id, true);
  }

  setIdeaBoardAvailability(id: string, available: boolean): Idea {
    const idea = this.requireIdea(id);
    const timestamp = nowIso();

    if (available) {
      idea.status = "ready";
      idea.updatedAt = timestamp;
      this.ensureBoardCard(idea, { githubMode: "local" }, timestamp);
      this.addActivity(
        "idea.ready",
        `Marked idea ready: ${idea.title}`,
        timestamp,
      );
      this.save();
      return clone(idea);
    }

    this.data.boardCards = this.data.boardCards.filter(
      (card) => card.ideaId !== idea.id,
    );
    idea.status =
      idea.details || idea.acceptanceCriteria.length > 0 ? "refining" : "idea";
    idea.updatedAt = timestamp;
    this.addActivity(
      "idea.not_ready",
      `Removed idea from board: ${idea.title}`,
      timestamp,
    );
    this.save();
    return clone(idea);
  }

  createBoardCardFromIdea(
    id: string,
    options: CreateBoardCardOptions,
  ): BoardCard {
    const idea = this.requireIdea(id);
    if (idea.status !== "ready") {
      throw new Error("Only ready ideas can be moved to the board");
    }

    const existing = this.ensureBoardCard(idea, options);
    this.save();
    return clone(existing);
  }

  private ensureBoardCard(
    idea: Idea,
    options: CreateBoardCardOptions,
    timestamp = nowIso(),
  ): BoardCard {
    const existing = this.data.boardCards.find(
      (card) => card.ideaId === idea.id,
    );
    if (existing) {
      this.syncBoardCardFromIdea(idea, timestamp);
      this.applyGitHubIssue(existing, options);
      this.applyGitHubIssue(idea, options);
      existing.updatedAt = timestamp;
      idea.updatedAt = existing.updatedAt;
      return existing;
    }

    const card: BoardCard = {
      id: randomUUID(),
      projectId: idea.projectId,
      title: idea.title,
      details: boardCardDetailsForIdea(idea),
      column: "ready",
      ideaId: idea.id,
      repositoryLocalPath: idea.repositoryLocalPath,
      repositoryRemoteUrl: idea.repositoryRemoteUrl,
      labels: [...idea.labels],
      dependsOn: [...idea.dependsOn],
      blockedBy: [...idea.blockedBy],
      readinessScore: idea.readinessScore,
      readinessReason: idea.readinessReason,
      readinessEvaluatedAt: idea.readinessEvaluatedAt,
      createdAt: timestamp,
      updatedAt: timestamp,
    };
    this.applyGitHubIssue(card, options);
    this.applyGitHubIssue(idea, options);

    this.data.boardCards.push(card);
    this.addActivity(
      "board.created",
      `Created board card: ${card.title}`,
      timestamp,
    );
    return card;
  }

  listBoardCards(projectId?: string): BoardCard[] {
    const filtered = projectId
      ? this.data.boardCards.filter(
          (card) => card.projectId === this.requireProject(projectId).id,
        )
      : this.data.boardCards;
    return clone(filtered).sort(sortUpdatedDesc);
  }

  getBoardColumns(projectId?: string): BoardColumns {
    const scopedProjectId = projectId
      ? this.requireProject(projectId).id
      : undefined;
    const columns: BoardColumns = {
      ready: [],
      planned: [],
      in_progress: [],
      review: [],
      done: [],
    };
    for (const card of this.data.boardCards) {
      if (scopedProjectId && card.projectId !== scopedProjectId) {
        continue;
      }
      columns[card.column].push(clone(card));
    }
    for (const column of boardColumns) {
      columns[column].sort(sortUpdatedDesc);
    }
    return columns;
  }

  moveBoardCard(id: string, column: BoardColumn): BoardCard {
    const card = this.requireBoardCard(id);
    card.column = column;
    card.updatedAt = nowIso();
    this.addActivity("board.moved", `Moved card to ${column}: ${card.title}`);
    this.save();
    return clone(card);
  }

  updateBoardCard(id: string, input: UpdateBoardCardInput): BoardCard {
    const card = this.requireBoardCard(id);
    if (input.column !== undefined) {
      card.column = input.column;
    }
    if (input.branchName !== undefined) {
      card.branchName = normalizeOptionalText(input.branchName);
    }
    if (input.details !== undefined) {
      card.details = input.details.trim();
    }
    if (input.repositoryLocalPath !== undefined) {
      card.repositoryLocalPath = normalizeOptionalText(
        input.repositoryLocalPath,
      );
    }
    if (input.repositoryRemoteUrl !== undefined) {
      card.repositoryRemoteUrl = normalizeOptionalText(
        input.repositoryRemoteUrl,
      );
    }
    card.updatedAt = nowIso();
    this.addActivity("board.updated", `Updated board card: ${card.title}`);
    this.save();
    return clone(card);
  }

  setCardReadiness(id: string, input: SetCardReadinessInput): BoardCard {
    const card = this.requireBoardCard(id);
    if (card.ideaId) {
      this.setIdeaReadiness(card.ideaId, input);
      return clone(this.requireBoardCard(id));
    }
    const { score, reason } = normalizeReadinessInput(input);
    card.readinessScore = score;
    card.readinessReason = reason;
    card.readinessEvaluatedAt = nowIso();
    this.addActivity(
      "board.readiness",
      `Set readiness ${score}/10: ${card.title}`,
    );
    this.save();
    return clone(card);
  }

  setIdeaReadiness(id: string, input: SetCardReadinessInput): Idea {
    const { score, reason } = normalizeReadinessInput(input);
    const idea = this.requireIdea(id);
    const timestamp = nowIso();
    idea.readinessScore = score;
    idea.readinessReason = reason;
    idea.readinessEvaluatedAt = timestamp;
    const card = this.data.boardCards.find((item) => item.ideaId === idea.id);
    if (card) {
      card.readinessScore = score;
      card.readinessReason = reason;
      card.readinessEvaluatedAt = timestamp;
    }
    this.data.readinessEvents.push({
      id: randomUUID(),
      ideaId: idea.id,
      score,
      reason,
      createdAt: timestamp,
    });
    this.addActivity(
      "idea.readiness",
      `Set readiness ${score}/10: ${idea.title}`,
    );
    this.save();
    return clone(idea);
  }

  listIdeaReadiness(id: string): ReadinessEvent[] {
    const idea = this.requireIdea(id);
    // Events are append-only, so insertion order is chronological; reverse for a
    // stable newest-first list even when timestamps collide within the same ms.
    return clone(
      this.data.readinessEvents.filter((event) => event.ideaId === idea.id),
    ).reverse();
  }

  listDocuments(projectId?: string): PlanDocument[] {
    const filtered = projectId
      ? this.data.documents.filter(
          (document) =>
            document.projectId === this.requireProject(projectId).id,
        )
      : this.data.documents;
    return clone(filtered).sort(sortUpdatedDesc);
  }

  createDocument(input: CreateDocumentInput): PlanDocument {
    assertTitle(input.title, "Document");
    const projectId = this.resolveProjectId(input.projectId);
    const timestamp = nowIso();
    const document: PlanDocument = {
      id: randomUUID(),
      projectId,
      title: input.title.trim(),
      kind: input.kind ?? "execution_plan",
      content: input.content?.trim() ?? "",
      linkedIdeaIds: normalizeList(input.linkedIdeaIds),
      linkedCardIds: normalizeList(input.linkedCardIds),
      createdAt: timestamp,
      updatedAt: timestamp,
    };

    this.data.documents.push(document);
    this.addActivity(
      "document.created",
      `Created document: ${document.title}`,
      timestamp,
    );
    this.save();
    return clone(document);
  }

  updateDocument(id: string, input: UpdateDocumentInput): PlanDocument {
    const document = this.requireDocument(id);
    if (input.title !== undefined) {
      assertTitle(input.title, "Document");
      document.title = input.title.trim();
    }
    if (input.kind !== undefined) {
      document.kind = input.kind;
    }
    if (input.content !== undefined) {
      document.content = input.content;
    }
    if (input.linkedIdeaIds !== undefined) {
      document.linkedIdeaIds = normalizeList(input.linkedIdeaIds);
    }
    if (input.linkedCardIds !== undefined) {
      document.linkedCardIds = normalizeList(input.linkedCardIds);
    }

    document.updatedAt = nowIso();
    this.addActivity("document.updated", `Updated document: ${document.title}`);
    this.save();
    return clone(document);
  }

  listActivity(): ActivityEvent[] {
    return clone(this.data.activity).sort((a, b) =>
      b.createdAt.localeCompare(a.createdAt),
    );
  }

  private load(): LoadResult {
    mkdirSync(dirname(this.filePath), { recursive: true });
    if (!existsSync(this.filePath)) {
      return { data: emptyData(), migrated: false };
    }

    const parsed = JSON.parse(
      readFileSync(this.filePath, "utf8"),
    ) as PersistedBoardData;
    const normalizedProjects = normalizeProjects(parsed.projects);
    const data: BoardData = {
      schemaVersion: 3,
      projects: normalizedProjects.projects,
      ideas: ((parsed.ideas ?? []) as Idea[]).map((idea) => ({
        ...idea,
        dependsOn: normalizeDependencyIds(idea.dependsOn),
        blocks: idea.blocks ?? [],
        blockedBy: idea.blockedBy ?? [],
      })),
      boardCards: ((parsed.boardCards ?? []) as BoardCard[]).map((card) => ({
        ...card,
        dependsOn: normalizeDependencyIds(card.dependsOn),
        blockedBy: card.blockedBy ?? [],
      })),
      readinessEvents: (parsed.readinessEvents ?? []) as ReadinessEvent[],
      documents: (parsed.documents ?? []) as PlanDocument[],
      activity: parsed.activity ?? [],
    };

    const ownershipMigrated = migrateProjectOwnership(data);
    const statusMigrated = normalizeIdeaStatuses(data);
    const taskNumberMigrated = normalizeIdeaTaskNumbers(data);
    let readinessBackfilled = false;
    for (const idea of data.ideas) {
      if (
        idea.readinessScore !== undefined &&
        !data.readinessEvents.some((event) => event.ideaId === idea.id)
      ) {
        data.readinessEvents.push({
          id: randomUUID(),
          ideaId: idea.id,
          score: idea.readinessScore,
          reason: idea.readinessReason ?? "",
          createdAt: idea.readinessEvaluatedAt ?? nowIso(),
        });
        readinessBackfilled = true;
      }
    }
    const migrated =
      parsed.schemaVersion !== 3 ||
      normalizedProjects.migrated ||
      ownershipMigrated ||
      statusMigrated ||
      taskNumberMigrated ||
      readinessBackfilled;

    return { data, migrated };
  }

  private save() {
    mkdirSync(dirname(this.filePath), { recursive: true });
    writeFileSync(
      this.filePath,
      `${JSON.stringify(this.data, null, 2)}\n`,
      "utf8",
    );
  }

  private requireIdea(id: string): Idea {
    const idea = this.data.ideas.find((item) => item.id === id);
    if (!idea) {
      throw new Error(`Idea not found: ${id}`);
    }
    return idea;
  }

  private requireProject(id: string): Project {
    const project = this.data.projects.find((item) => item.id === id);
    if (!project) {
      throw new Error(`Project not found: ${id}`);
    }
    return project;
  }

  private resolveProjectId(id: string | undefined): string {
    if (id !== undefined) {
      return this.requireProject(id).id;
    }

    const existing = this.data.projects[0];
    if (existing) {
      return existing.id;
    }

    const timestamp = nowIso();
    const project: Project = {
      id: randomUUID(),
      key: nextAvailableProjectKey(
        this.data.projects.map((item) => item.key),
        "GEN",
      ),
      title: "General",
      summary: defaultProjectSummary,
      createdAt: timestamp,
      updatedAt: timestamp,
    };
    this.data.projects.push(project);
    this.addActivity(
      "project.created",
      `Created project: ${project.title}`,
      timestamp,
    );
    return project.id;
  }

  private assertProjectKeyAvailable(key: string, currentProjectId?: string) {
    const existing = this.data.projects.find(
      (project) => project.key === key && project.id !== currentProjectId,
    );
    if (existing) {
      throw new Error(`Project ID ${key} is already used`);
    }
  }

  private nextTaskNumber(projectId: string): number {
    return (
      this.data.ideas
        .filter((idea) => idea.projectId === projectId)
        .reduce((highest, idea) => Math.max(highest, idea.taskNumber ?? 0), 0) +
      1
    );
  }

  private requireBoardCard(id: string): BoardCard {
    const card = this.data.boardCards.find((item) => item.id === id);
    if (!card) {
      throw new Error(`Board card not found: ${id}`);
    }
    return card;
  }

  private requireDocument(id: string): PlanDocument {
    const document = this.data.documents.find((item) => item.id === id);
    if (!document) {
      throw new Error(`Document not found: ${id}`);
    }
    return document;
  }

  private syncBoardCardFromIdea(idea: Idea, timestamp = nowIso()) {
    const card = this.data.boardCards.find((item) => item.ideaId === idea.id);
    if (!card) {
      return;
    }
    card.title = idea.title;
    card.details = boardCardDetailsForIdea(idea);
    card.labels = [...idea.labels];
    card.repositoryLocalPath = idea.repositoryLocalPath;
    card.repositoryRemoteUrl = idea.repositoryRemoteUrl;
    card.updatedAt = timestamp;
  }

  private applyGitHubIssue(
    entity: Pick<Idea | BoardCard, "githubIssueUrl" | "githubIssueNumber"> &
      Partial<Idea>,
    options: CreateBoardCardOptions,
  ) {
    if (options.githubMode === "github") {
      entity.githubIssueUrl = options.githubIssueUrl;
      entity.githubIssueNumber = options.githubIssueNumber;
    }
  }

  private addActivity(type: string, message: string, createdAt = nowIso()) {
    this.data.activity.push({
      id: randomUUID(),
      type,
      message,
      createdAt,
    });
    this.data.activity = this.data.activity.slice(-100);
  }
}

const sortUpdatedDesc = <T extends { updatedAt: string; title?: string }>(
  a: T,
  b: T,
) =>
  b.updatedAt.localeCompare(a.updatedAt) ||
  (a.title ?? "").localeCompare(b.title ?? "");

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
