import type { Pool, PoolClient } from "pg";

import { parseTaskReference } from "../shared/taskKeys.js";
import type { AccessContext } from "./store.js";

type Queryable = Pick<Pool | PoolClient, "query">;

/**
 * The project ids a caller may see, or null for an admin who may see all of
 * them. Resolution filters on this *before* reporting existence, so a scoped
 * caller cannot tell a foreign record apart from a missing one.
 */
const scopedProjectIds = (access: AccessContext): string[] | null =>
  access.kind === "admin" ? null : access.projectIds;

export const resolveProjectId = async (
  queryable: Queryable,
  access: AccessContext,
  reference: string,
): Promise<string> => {
  const value = reference.trim();
  const scope = scopedProjectIds(access);

  const { rows } = await queryable.query<{ id: string; key: string }>(
    `select id, key from projects
      where ($2::text[] is null or id = any($2::text[]))
        and (id = $1 or upper(key) = upper($1) or lower(title) = lower($1))
      order by key asc`,
    [value, scope],
  );

  if (rows.length === 0) {
    throw new Error(`Project not found: ${value}`);
  }

  // An id or key match is exact and wins outright; titles are not unique.
  const exact = rows.filter(
    (row) => row.id === value || row.key.toUpperCase() === value.toUpperCase(),
  );
  if (exact.length === 1) {
    return exact[0].id;
  }
  if (rows.length > 1) {
    const keys = rows.map((row) => row.key).join(", ");
    throw new Error(
      `Ambiguous project reference: ${value} matches ${rows.length} projects (${keys}); use the project key`,
    );
  }
  return rows[0].id;
};

export const resolveIdeaId = async (
  queryable: Queryable,
  access: AccessContext,
  reference: string,
): Promise<string> => {
  const value = reference.trim();
  const scope = scopedProjectIds(access);
  const parsed = parseTaskReference(value);

  const byId = await queryable.query<{ id: string }>(
    `select id from ideas
      where id = $1 and ($2::text[] is null or project_id = any($2::text[]))`,
    [value, scope],
  );
  if (byId.rows.length === 1) {
    return byId.rows[0].id;
  }

  if (parsed.kind === "key") {
    const { rows } = await queryable.query<{ id: string }>(
      `select ideas.id from ideas
         join projects on projects.id = ideas.project_id
        where upper(projects.key) = $1
          and ideas.task_number = $2
          and ($3::text[] is null or ideas.project_id = any($3::text[]))`,
      [parsed.projectKey, parsed.taskNumber, scope],
    );
    if (rows.length === 1) {
      return rows[0].id;
    }
    throw new Error(
      `Task not found: ${value} (project ${parsed.projectKey}, task ${parsed.taskNumber})`,
    );
  }

  if (parsed.kind === "number") {
    const { rows } = await queryable.query<{ id: string; key: string }>(
      `select ideas.id, projects.key from ideas
         join projects on projects.id = ideas.project_id
        where ideas.task_number = $1
          and ($2::text[] is null or ideas.project_id = any($2::text[]))
        order by projects.key asc`,
      [parsed.taskNumber, scope],
    );
    if (rows.length === 1) {
      return rows[0].id;
    }
    if (rows.length > 1) {
      throw new Error(
        `Ambiguous task reference: ${value} matches ${rows.length} projects in scope; use a project key such as ${rows[0].key}-${parsed.taskNumber}`,
      );
    }
  }

  throw new Error(`Task not found: ${value}`);
};

export const resolveBoardCardId = async (
  queryable: Queryable,
  access: AccessContext,
  reference: string,
): Promise<string> => {
  const value = reference.trim();
  const scope = scopedProjectIds(access);

  const byId = await queryable.query<{ id: string }>(
    `select id from board_cards
      where id = $1 and ($2::text[] is null or project_id = any($2::text[]))`,
    [value, scope],
  );
  if (byId.rows.length === 1) {
    return byId.rows[0].id;
  }

  const ideaId = await resolveIdeaId(queryable, access, value).catch(
    () => null,
  );
  if (ideaId) {
    // board_cards.idea_id carries no unique constraint, so pick deterministically.
    const { rows } = await queryable.query<{ id: string }>(
      "select id from board_cards where idea_id = $1 order by created_at asc, id asc limit 1",
      [ideaId],
    );
    if (rows.length === 1) {
      return rows[0].id;
    }
  }

  throw new Error(`Board card not found: ${value}`);
};
