# Project Data Import and Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin browser workflow that exports one project as a portable JSON bundle and transactionally creates or replaces a project by key when that bundle is imported.

**Architecture:** A strict shared Zod contract defines the versioned bundle and validates all internal links. `PostgresBoardStore` owns scoped export and transactional import so database identity, collision checks, and rollback stay below the HTTP layer; Express adds admin-only transport endpoints, and focused client helpers support file parsing/downloading while the Projects screen owns the modal workflow.

**Tech Stack:** TypeScript, React 18, Mantine 8, Express 5, Zod 3, PostgreSQL 16, Vitest, Testing Library, Supertest.

---

## File Map

- Create `src/shared/projectBundle.ts`: strict V1 bundle schemas, parsing, cross-reference validation, and shared request/result types.
- Modify `src/shared/types.ts`: project bundle record and transfer result types used across client and server.
- Modify `src/server/store.ts`: add project export/import operations to `BoardDataStore`.
- Modify `src/server/storage.ts`: implement scoped export, collision detection, new-project import, and transactional replacement.
- Modify `src/server/app.ts`: add admin endpoints, a 10 MiB parser for import, download headers, and HTTP error mapping.
- Create `src/client/projectTransfer.ts`: parse selected files, build previews, and download response blobs.
- Modify `src/client/main.tsx`: add Import/Export controls, preview/confirmation modal, loading, success, and error states.
- Create `tests/project-bundle.test.ts`: contract and semantic validation tests.
- Create `tests/project-transfer.test.ts`: PostgreSQL export/import integration tests.
- Modify `tests/api.test.ts`: authorization, transport, response, and size-limit coverage.
- Create `tests/project-transfer-utils.test.ts`: client helper tests.
- Create `tests/ui-project-transfer.test.tsx`: Projects-screen import/export interaction tests.
- Modify `README.md`: document browser workflow and REST endpoints.

### Task 1: Define and validate the portable bundle contract

**Files:**
- Modify: `src/shared/types.ts`
- Create: `src/shared/projectBundle.ts`
- Create: `tests/project-bundle.test.ts`

- [ ] **Step 1: Write failing contract tests**

Create `tests/project-bundle.test.ts` with one valid fixture and focused mutations:

```ts
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
    { name: "unsupported version", mutate: (bundle) => ({ ...bundle, bundleVersion: 2 }) },
    { name: "foreign idea", mutate: (bundle) => ({ ...bundle, ideas: [{ ...bundle.ideas[0], projectId: "other" }, bundle.ideas[1]] }) },
    { name: "missing dependency", mutate: (bundle) => ({ ...bundle, ideas: [bundle.ideas[0], { ...bundle.ideas[1], dependsOn: ["missing"] }] }) },
    { name: "dependency cycle", mutate: (bundle) => ({ ...bundle, ideas: [{ ...bundle.ideas[0], dependsOn: ["idea-2"] }, bundle.ideas[1]] }) },
    { name: "foreign card link", mutate: (bundle) => ({ ...bundle, boardCards: [{ ...bundle.boardCards[0], ideaId: "missing" }] }) },
    { name: "foreign document link", mutate: (bundle) => ({ ...bundle, documents: [{ ...bundle.documents[0], linkedIdeaIds: ["missing"] }] }) },
    { name: "duplicate task number", mutate: (bundle) => ({ ...bundle, ideas: [bundle.ideas[0], { ...bundle.ideas[1], taskNumber: 1 }] }) },
  ];

  it.each(invalidCases)("rejects $name", ({ mutate }) => {
    expect(() => parseProjectBundle(mutate(validBundle()))).toThrow();
  });

  it("rejects unknown fields", () => {
    expect(() => parseProjectBundle({ ...validBundle(), mystery: true })).toThrow();
  });
});
```

- [ ] **Step 2: Run the contract test and verify RED**

Run: `npx vitest run tests/project-bundle.test.ts`

Expected: FAIL because `src/shared/projectBundle.ts` and `ProjectBundle` do not exist.

- [ ] **Step 3: Add shared transfer types**

Append these declarations to `src/shared/types.ts`:

```ts
export type ProjectBundle = {
  bundleVersion: 1;
  exportedAt: string;
  project: Project;
  ideas: Idea[];
  boardCards: BoardCard[];
  readinessEvents: ReadinessEvent[];
  documents: PlanDocument[];
};

export type ImportProjectOptions = {
  replaceExisting: boolean;
};

export type ImportProjectResult = {
  item: Project;
  replaced: boolean;
};
```

- [ ] **Step 4: Implement strict structural and relationship validation**

Create `src/shared/projectBundle.ts`. Define strict Zod object schemas for every field in `Project`, `Idea`, `BoardCard`, `ReadinessEvent`, and `PlanDocument`, using the exported enums:

Export this API and implement semantic checks with `superRefine`:

```ts
import { z } from "zod";
import { boardColumns, documentKinds, ideaStatuses } from "./types.js";
import type { ProjectBundle } from "./types.js";

const timestampSchema = z.string().datetime({ offset: true });
const optionalTextSchema = z.string().optional();
const optionalIntegerSchema = z.number().int().optional();
const optionalReadinessScoreSchema = z.number().int().min(1).max(10).optional();

const projectSchema = z.object({
  id: z.string().min(1),
  key: z.string().regex(/^[A-Z]{1,3}$/),
  title: z.string().trim().min(1),
  summary: z.string(),
  createdAt: timestampSchema,
  updatedAt: timestampSchema,
}).strict();

const ideaSchema = z.object({
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
  readinessScore: optionalReadinessScoreSchema,
  readinessReason: optionalTextSchema,
  readinessEvaluatedAt: timestampSchema.optional(),
  createdAt: timestampSchema,
  updatedAt: timestampSchema,
}).strict();

const boardCardSchema = z.object({
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
  labels: z.array(z.string()),
  dependsOn: z.array(z.string().min(1)),
  blockedBy: z.array(z.string().min(1)),
  readinessScore: optionalReadinessScoreSchema,
  readinessReason: optionalTextSchema,
  readinessEvaluatedAt: timestampSchema.optional(),
  createdAt: timestampSchema,
  updatedAt: timestampSchema,
}).strict();

const readinessEventSchema = z.object({
  id: z.string().min(1),
  ideaId: z.string().min(1),
  score: z.number().int().min(1).max(10),
  reason: z.string(),
  createdAt: timestampSchema,
}).strict();

const documentSchema = z.object({
  id: z.string().min(1),
  projectId: z.string().min(1),
  title: z.string().trim().min(1),
  kind: z.enum(documentKinds),
  content: z.string(),
  linkedIdeaIds: z.array(z.string().min(1)),
  linkedCardIds: z.array(z.string().min(1)),
  createdAt: timestampSchema,
  updatedAt: timestampSchema,
}).strict();

const addIssue = (ctx: z.RefinementCtx, path: (string | number)[], message: string) => {
  ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
};

const duplicateValues = <T>(values: T[]): Set<T> => {
  const seen = new Set<T>();
  const duplicates = new Set<T>();
  for (const value of values) {
    if (seen.has(value)) duplicates.add(value);
    seen.add(value);
  }
  return duplicates;
};

const cyclicIdeaIds = (bundle: ProjectBundle): Set<string> => {
  const graph = new Map(bundle.ideas.map((idea) => [idea.id, idea.dependsOn]));
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const cyclic = new Set<string>();
  const visit = (id: string): boolean => {
    if (visiting.has(id)) {
      cyclic.add(id);
      return true;
    }
    if (visited.has(id)) return false;
    visiting.add(id);
    const hasCycle = (graph.get(id) ?? []).some((dependencyId) => visit(dependencyId));
    visiting.delete(id);
    visited.add(id);
    if (hasCycle) cyclic.add(id);
    return hasCycle;
  };
  for (const id of graph.keys()) visit(id);
  return cyclic;
};

const validateRelationships = (bundle: ProjectBundle, ctx: z.RefinementCtx) => {
  const ideaIds = new Set(bundle.ideas.map((idea) => idea.id));
  const cardIds = new Set(bundle.boardCards.map((card) => card.id));

  for (const id of duplicateValues(bundle.ideas.map((idea) => idea.id)))
    addIssue(ctx, ["ideas"], `Duplicate idea id: ${id}`);
  for (const number of duplicateValues(bundle.ideas.map((idea) => idea.taskNumber)))
    addIssue(ctx, ["ideas"], `Duplicate task number: ${number}`);
  for (const id of duplicateValues(bundle.boardCards.map((card) => card.id)))
    addIssue(ctx, ["boardCards"], `Duplicate board card id: ${id}`);
  for (const id of duplicateValues(bundle.readinessEvents.map((event) => event.id)))
    addIssue(ctx, ["readinessEvents"], `Duplicate readiness event id: ${id}`);
  for (const id of duplicateValues(bundle.documents.map((document) => document.id)))
    addIssue(ctx, ["documents"], `Duplicate document id: ${id}`);

  bundle.ideas.forEach((idea, index) => {
    if (idea.projectId !== bundle.project.id)
      addIssue(ctx, ["ideas", index, "projectId"], "Idea belongs to another project");
    idea.dependsOn.forEach((id) => {
      if (!ideaIds.has(id)) addIssue(ctx, ["ideas", index, "dependsOn"], `Dependency is not in bundle: ${id}`);
    });
  });
  for (const id of cyclicIdeaIds(bundle))
    addIssue(ctx, ["ideas"], `Dependency cycle includes: ${id}`);

  bundle.boardCards.forEach((card, index) => {
    if (card.projectId !== bundle.project.id)
      addIssue(ctx, ["boardCards", index, "projectId"], "Board card belongs to another project");
    if (card.ideaId && !ideaIds.has(card.ideaId))
      addIssue(ctx, ["boardCards", index, "ideaId"], `Linked idea is not in bundle: ${card.ideaId}`);
  });
  bundle.readinessEvents.forEach((event, index) => {
    if (!ideaIds.has(event.ideaId))
      addIssue(ctx, ["readinessEvents", index, "ideaId"], `Readiness idea is not in bundle: ${event.ideaId}`);
  });
  bundle.documents.forEach((document, index) => {
    if (document.projectId !== bundle.project.id)
      addIssue(ctx, ["documents", index, "projectId"], "Document belongs to another project");
    document.linkedIdeaIds.forEach((id) => {
      if (!ideaIds.has(id)) addIssue(ctx, ["documents", index, "linkedIdeaIds"], `Linked idea is not in bundle: ${id}`);
    });
    document.linkedCardIds.forEach((id) => {
      if (!cardIds.has(id)) addIssue(ctx, ["documents", index, "linkedCardIds"], `Linked card is not in bundle: ${id}`);
    });
  });
};

export const projectBundleSchema = z
  .object({
    bundleVersion: z.literal(1),
    exportedAt: z.string().datetime({ offset: true }),
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
```

- [ ] **Step 5: Run contract tests and typecheck**

Run: `npx vitest run tests/project-bundle.test.ts && npm run typecheck`

Expected: all contract tests PASS and both TypeScript projects exit 0.

- [ ] **Step 6: Commit the contract**

```bash
git add src/shared/types.ts src/shared/projectBundle.ts tests/project-bundle.test.ts
git commit -m "Add project bundle contract"
```

### Task 2: Export a fully scoped project bundle

**Files:**
- Modify: `src/server/store.ts`
- Modify: `src/server/storage.ts`
- Create: `tests/project-transfer.test.ts`

- [ ] **Step 1: Write the failing scoped-export integration test**

Create `tests/project-transfer.test.ts` using `createTestStore` and `adminAccess`. Build two projects, add tasks in both, make one APP task ready, move its card to `planned`, set readiness, add a dependency and linked document, then assert:

```ts
const bundle = await store.exportProject(project.id);

expect(bundle).toMatchObject({
  bundleVersion: 1,
  project: { id: project.id, key: "APP" },
});
expect(bundle.ideas.map((idea) => idea.projectId)).toEqual([
  project.id,
  project.id,
]);
expect(bundle.ideas.find((idea) => idea.title === "Feature")?.dependsOn).toEqual([
  foundation.id,
]);
expect(bundle.boardCards).toHaveLength(1);
expect(bundle.boardCards[0].column).toBe("planned");
expect(bundle.readinessEvents).toHaveLength(1);
expect(bundle.documents[0]).toMatchObject({
  linkedIdeaIds: [foundation.id],
  linkedCardIds: [bundle.boardCards[0].id],
});
expect(JSON.stringify(bundle)).not.toContain("Other project task");
await expect(store.exportProject("missing")).rejects.toThrow(
  "Project not found: missing",
);
```

- [ ] **Step 2: Run the export test and verify RED**

Run: `npx vitest run tests/project-transfer.test.ts -t "exports one self-contained project"`

Expected: FAIL because `exportProject` is not defined.

- [ ] **Step 3: Add the store interface operation**

Add to `BoardDataStore` in `src/server/store.ts` and import `ProjectBundle`:

```ts
exportProject(id: string): Promise<ProjectBundle>;
```

Task 3 adds the import operation after its failing test establishes the desired API.

- [ ] **Step 4: Implement scoped export in `PostgresBoardStore`**

Add imports for `ProjectBundle`, then implement the method next to `getState`:

```ts
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
```

Import `tokenAccess` from `store.ts` and `parseProjectBundle` from `shared/projectBundle.ts`. Calling the shared parser here guarantees the server never emits an invalid bundle.

- [ ] **Step 5: Run the scoped export test**

Run: `npx vitest run tests/project-transfer.test.ts -t "exports one self-contained project"`

Expected: PASS.

- [ ] **Step 6: Commit scoped export**

```bash
git add src/server/store.ts src/server/storage.ts tests/project-transfer.test.ts
git commit -m "Add scoped project export"
```

### Task 3: Import a bundle as a new project

**Files:**
- Modify: `src/server/storage.ts`
- Modify: `tests/project-transfer.test.ts`

- [ ] **Step 1: Write the failing new-project round-trip test**

Export the APP fixture from Task 2, reset all database tables with `resetDatabase(pool)`, reinitialize the schema, then assert:

```ts
const result = await store.importProject(bundle, { replaceExisting: false });

expect(result).toEqual({ item: bundle.project, replaced: false });
expect(await store.exportProject(bundle.project.id)).toMatchObject({
  project: bundle.project,
  ideas: bundle.ideas,
  boardCards: bundle.boardCards,
  readinessEvents: bundle.readinessEvents,
  documents: bundle.documents,
});
expect((await store.listActivity(admin))[0]).toMatchObject({
  type: "project.imported",
  message: "Imported project: Application",
});
```

Compare `exportedAt` separately because the second export creates a fresh timestamp.

- [ ] **Step 2: Run the round-trip test and verify RED**

Run: `npx vitest run tests/project-transfer.test.ts -t "imports a bundle as a new project"`

Expected: FAIL because `importProject` is not implemented.

- [ ] **Step 3: Implement the transactional import entry point and insert helpers**

Add the following operation and type imports to `BoardDataStore` in `src/server/store.ts`:

```ts
importProject(
  bundle: ProjectBundle,
  options: ImportProjectOptions,
): Promise<ImportProjectResult>;
```

In `PostgresBoardStore`, parse at the boundary, open a client, and use one transaction:

```ts
async importProject(
  input: ProjectBundle,
  options: ImportProjectOptions,
): Promise<ImportProjectResult> {
  const bundle = parseProjectBundle(input);
  const client = await this.pool.connect();
  try {
    await client.query("begin");
    const existingByKey = await client.query<ProjectRow>(
      "select * from projects where key = $1",
      [bundle.project.key],
    );
    const existing = existingByKey.rows[0];
    if (existing) {
      throw new Error(`Project key ${bundle.project.key} already exists; replacement confirmation is required`);
    }
    const idCollision = await client.query<ProjectRow>(
      "select * from projects where id = $1",
      [bundle.project.id],
    );
    if (idCollision.rowCount) {
      throw new Error(`Project ID is already used: ${bundle.project.id}`);
    }

    await this.insertImportedProject(client, bundle, bundle.project.id);
    await this.addActivity(
      client,
      "project.imported",
      `Imported project: ${bundle.project.title}`,
      nowIso(),
    );
    await client.query("commit");
    return { item: bundle.project, replaced: false };
  } catch (error) {
    await client.query("rollback");
    throw error;
  } finally {
    client.release();
  }
}
```

Implement `insertImportedProject(client, bundle, destinationProjectId)` to insert the `projects` row and then call `insertImportedChildren(client, bundle, destinationProjectId)`. Implement `insertImportedChildren` with explicit parameterized inserts in this order:

1. `ideas` with all persisted fields, rewriting `project_id` to `destinationProjectId`; serialize labels and acceptance criteria with `JSON.stringify`.
2. `task_dependencies` from each idea's deduplicated `dependsOn` list.
3. `board_cards`, rewriting `project_id`; serialize labels.
4. `idea_readiness_events`.
5. `documents`, rewriting `project_id`; serialize linked IDs.

Use the complete column lists already present in `src/server/jsonImport.ts`, including repository, GitHub, and readiness columns. Do not insert derived `blocks`, `blockedBy`, or card dependency fields because reads derive them from `task_dependencies`.

- [ ] **Step 4: Run round-trip, export, and typecheck**

Run: `npx vitest run tests/project-transfer.test.ts && npm run typecheck`

Expected: transfer tests PASS and both TypeScript projects exit 0.

- [ ] **Step 5: Commit new-project import**

```bash
git add src/server/store.ts src/server/storage.ts tests/project-transfer.test.ts
git commit -m "Add new project bundle import"
```

### Task 4: Add confirmed replacement, collision checks, and rollback guarantees

**Files:**
- Modify: `src/server/storage.ts`
- Modify: `tests/project-transfer.test.ts`

- [ ] **Step 1: Write failing replacement tests**

Add tests covering all of these assertions:

```ts
await expect(
  store.importProject(bundle, { replaceExisting: false }),
).rejects.toThrow("replacement confirmation is required");

const replaced = await store.importProject(bundle, { replaceExisting: true });
expect(replaced.replaced).toBe(true);
expect(replaced.item.id).toBe(destination.id);
expect(replaced.item.title).toBe(bundle.project.title);
expect((await store.listIdeas(admin, destination.id)).map((idea) => idea.title)).not.toContain("Old destination task");
expect((await store.listIdeas(admin, destination.id)).map((idea) => idea.projectId)).toEqual(
  bundle.ideas.map(() => destination.id),
);
expect((await store.listApiTokens())[0].projects.map((project) => project.id)).toContain(destination.id);
```

Create a second project and task, mutate the bundle so its first idea uses that unrelated task's ID, and update the bundle's dependency, card, readiness, and document references to keep the bundle internally valid. Assert replacement rejects with `Idea ID is already used by another project`. Verify the original destination task is still present afterward.

- [ ] **Step 2: Add a failing forced-database-error rollback test**

Install a temporary trigger in the test database that rejects insertion of an imported idea title, then verify the old destination survives:

```ts
await pool.query(`
  create or replace function reject_project_import_test() returns trigger as $$
  begin
    if new.title = 'Force transaction failure' then
      raise exception 'forced project import failure';
    end if;
    return new;
  end;
  $$ language plpgsql;
  create trigger reject_project_import_test
  before insert on ideas
  for each row execute function reject_project_import_test();
`);

const failingBundle = {
  ...bundle,
  ideas: bundle.ideas.map((idea, index) =>
    index === 0 ? { ...idea, title: "Force transaction failure" } : idea,
  ),
};
await expect(
  store.importProject(failingBundle, { replaceExisting: true }),
).rejects.toThrow("forced project import failure");
expect((await store.listIdeas(admin, destination.id)).map((idea) => idea.title)).toContain(
  "Old destination task",
);
```

Drop the trigger and function in a `finally` block so later tests cannot inherit it.

- [ ] **Step 3: Run replacement tests and verify RED**

Run: `npx vitest run tests/project-transfer.test.ts -t "replace|collision|rolls back"`

Expected: FAIL because existing-key replacement and cross-project collision handling are absent.

- [ ] **Step 4: Implement replacement and pre-delete collision checks**

Before deleting destination data, query each table by imported IDs and reject rows whose owning project is not the destination. For readiness events, join through `ideas` to obtain project ownership. Use messages shaped as `<Entity> ID is already used by another project: <id>` so the API can return `409` consistently.

Replace the existing-project branch in `importProject` with:

```ts
if (existing && !options.replaceExisting) {
  throw new Error(
    `Project key ${bundle.project.key} already exists; replacement confirmation is required`,
  );
}

const destinationId = existing?.id ?? bundle.project.id;
await this.assertImportedIdsAvailable(client, bundle, destinationId);

if (existing) {
  await client.query("delete from board_cards where project_id = $1", [destinationId]);
  await client.query("delete from documents where project_id = $1", [destinationId]);
  await client.query("delete from ideas where project_id = $1", [destinationId]);
  await client.query(
    `update projects
     set title = $2, summary = $3, created_at = $4, updated_at = $5
     where id = $1`,
    [
      destinationId,
      bundle.project.title,
      bundle.project.summary,
      bundle.project.createdAt,
      bundle.project.updatedAt,
    ],
  );
  await this.insertImportedChildren(client, bundle, destinationId);
} else {
  await this.insertImportedProject(client, bundle, destinationId);
}
```

Return a `Project` whose ID is `destinationId`, key is the imported key, and other fields come from the bundle. Keep the activity insert before `commit`.

- [ ] **Step 5: Run all transfer tests**

Run: `npx vitest run tests/project-transfer.test.ts`

Expected: all export, create, replace, collision, and rollback tests PASS.

- [ ] **Step 6: Commit replacement behavior**

```bash
git add src/server/storage.ts tests/project-transfer.test.ts
git commit -m "Add transactional project replacement"
```

### Task 5: Expose admin-only export and import REST endpoints

**Files:**
- Modify: `src/server/app.ts`
- Modify: `tests/api.test.ts`

- [ ] **Step 1: Write failing API authorization and export tests**

Add a test that creates a project through an authenticated agent, then verifies:

```ts
await request(app).get(`/api/projects/${projectId}/export`).expect(401);
await request(app)
  .get(`/api/projects/${projectId}/export`)
  .set("Authorization", `Bearer ${projectToken}`)
  .expect(403);

const response = await agent
  .get(`/api/projects/${projectId}/export`)
  .expect(200)
  .expect("Content-Type", /application\/json/)
  .expect("Content-Disposition", 'attachment; filename="APP-project.json"');
expect(response.body).toMatchObject({
  bundleVersion: 1,
  project: { id: projectId, key: "APP" },
});
await agent.get("/api/projects/missing/export").expect(404);
```

- [ ] **Step 2: Write failing import response tests**

Post the exported bundle into an empty board and expect `201` with `{ item, replaced: false }`. Post it again with `replaceExisting: false` and expect `409`; then post with `replaceExisting: true` and expect `200` with `{ item, replaced: true }`. Also verify unauthenticated and bearer-token requests receive `401` and `403`.

Add malformed and size tests:

```ts
await agent
  .post("/api/projects/import")
  .send({ bundle: { bundleVersion: 99 }, replaceExisting: false })
  .expect(400);

await agent
  .post("/api/projects/import")
  .set("Content-Type", "application/json")
  .send(JSON.stringify({ bundle: { padding: "x".repeat(10 * 1024 * 1024) } }))
  .expect(413);
```

- [ ] **Step 3: Run focused API tests and verify RED**

Run: `npx vitest run tests/api.test.ts -t "project export|project import"`

Expected: FAIL with route-not-found responses.

- [ ] **Step 4: Add route-specific parsing and endpoints**

Before the existing 2 MiB JSON middleware, register the larger parser:

```ts
app.use("/api/projects/import", express.json({ limit: "10mb" }));
app.use(express.json({ limit: "2mb" }));
```

Register import before `/:id` routes and use the shared parser:

```ts
const importProjectRequestSchema = z
  .object({
    bundle: projectBundleSchema,
    replaceExisting: z.boolean().default(false),
  })
  .strict();

app.post(
  "/api/projects/import",
  requireAdmin(store, sessions),
  asyncHandler(async (req, res) => {
    const { bundle, replaceExisting } = importProjectRequestSchema.parse(req.body);
    const result = await store.importProject(bundle, { replaceExisting });
    res.status(result.replaced ? 200 : 201).json(result);
  }),
);

app.get(
  "/api/projects/:id/export",
  requireAdmin(store, sessions),
  asyncHandler(async (req, res) => {
    const bundle = await store.exportProject(routeParam(req.params.id));
    res
      .attachment(`${bundle.project.key}-project.json`)
      .type("application/json")
      .send(JSON.stringify(bundle, null, 2));
  }),
);
```

- [ ] **Step 5: Map transfer and parser errors**

In `errorHandler`, inspect the Express body-parser error before message matching:

```ts
if (
  typeof error === "object" &&
  error !== null &&
  "type" in error &&
  error.type === "entity.too.large"
) {
  res.status(413).json({ error: "Project import file must be 10 MiB or smaller" });
  return;
}
```

Map messages containing `replacement confirmation is required` or `ID is already used by another project` to `409`. Zod remains `400`, and existing not-found matching remains `404`.

- [ ] **Step 6: Run API and transfer tests**

Run: `npx vitest run tests/api.test.ts tests/project-transfer.test.ts && npm run typecheck`

Expected: both test files PASS and typecheck exits 0.

- [ ] **Step 7: Commit the REST API**

```bash
git add src/server/app.ts tests/api.test.ts
git commit -m "Add project transfer API"
```

### Task 6: Add testable browser file helpers

**Files:**
- Create: `src/client/projectTransfer.ts`
- Create: `tests/project-transfer-utils.test.ts`

- [ ] **Step 1: Write failing helper tests**

Create `tests/project-transfer-utils.test.ts` using the valid bundle fixture shape from Task 1. Cover parsing, invalid JSON, preview counts, destination matching by key, filename generation, and download cleanup:

```ts
expect(parseProjectBundleText(JSON.stringify(bundle))).toEqual(bundle);
expect(() => parseProjectBundleText("not JSON")).toThrow("Project file is not valid JSON");
expect(projectBundlePreview(bundle, [{ ...bundle.project, id: "destination" }])).toEqual({
  key: "APP",
  title: "Application",
  tasks: 2,
  cards: 1,
  documents: 1,
  existingProjectId: "destination",
});
expect(projectExportFileName(bundle.project)).toBe("APP-project.json");
```

For `downloadResponse`, inject a document-like object, stub `URL.createObjectURL` and `URL.revokeObjectURL`, and assert the temporary anchor receives the blob URL, filename, and one click before removal.

- [ ] **Step 2: Run helper tests and verify RED**

Run: `npx vitest run tests/project-transfer-utils.test.ts`

Expected: FAIL because `src/client/projectTransfer.ts` does not exist.

- [ ] **Step 3: Implement the focused helpers**

Create `src/client/projectTransfer.ts` with this public API:

```ts
import { parseProjectBundle } from "../shared/projectBundle.js";
import type { Project, ProjectBundle } from "../shared/types.js";

export const parseProjectBundleText = (text: string): ProjectBundle => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("Project file is not valid JSON");
  }
  try {
    return parseProjectBundle(parsed);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Validation failed";
    throw new Error(`Project file is invalid: ${message}`);
  }
};

export const projectBundlePreview = (
  bundle: ProjectBundle,
  projects: Project[],
) => ({
  key: bundle.project.key,
  title: bundle.project.title,
  tasks: bundle.ideas.length,
  cards: bundle.boardCards.length,
  documents: bundle.documents.length,
  existingProjectId: projects.find((project) => project.key === bundle.project.key)?.id,
});

export const projectExportFileName = (project: Project) =>
  `${project.key}-project.json`;

export const downloadResponse = async (
  response: Response,
  filename: string,
): Promise<void> => {
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
};
```

- [ ] **Step 4: Run helper tests and typecheck**

Run: `npx vitest run tests/project-transfer-utils.test.ts && npm run typecheck`

Expected: helper tests PASS and both TypeScript projects exit 0.

- [ ] **Step 5: Commit helpers**

```bash
git add src/client/projectTransfer.ts tests/project-transfer-utils.test.ts
git commit -m "Add project transfer browser helpers"
```

### Task 7: Build the Projects-screen import/export workflow

**Files:**
- Modify: `src/client/main.tsx`
- Create: `tests/ui-project-transfer.test.tsx`

- [ ] **Step 1: Write the failing export interaction test**

Create `tests/ui-project-transfer.test.tsx` with the jsdom setup used by `tests/ui-auth-token.test.tsx`. Stub authenticated state requests and an export response. Render `AppShell`, click the APP card's Export button, and assert fetch received `/api/projects/project-1/export` with credentials. Stub `URL.createObjectURL`/`revokeObjectURL` and assert the download helper is invoked with `APP-project.json`.

- [ ] **Step 2: Write failing import preview and confirmation tests**

Click **Import Project**, upload a JSON `File`, and assert the modal shows:

```ts
expect(await screen.findByText("Application (APP)")).toBeTruthy();
expect(screen.getByText("2 tasks")).toBeTruthy();
expect(screen.getByText("1 board card")).toBeTruthy();
expect(screen.getByText("1 document")).toBeTruthy();
expect(screen.getByText(/will replace all current project data/i)).toBeTruthy();
expect(screen.getByRole("button", { name: "Replace Project" })).toBeTruthy();
```

Click **Replace Project** and assert the POST body is:

```ts
{
  bundle,
  replaceExisting: true,
}
```

Assert the button is disabled while the promise is pending, the app reloads `/api/projects`, `/api/ideas`, `/api/board`, and `/api/documents` after success, the modal closes, and a green success alert appears.

Add a new-key bundle test that shows **Import Project**, does not show the replacement warning, and sends `replaceExisting: false`. Add malformed-file and failed-POST tests that keep the modal open and show the error text.

- [ ] **Step 3: Run UI tests and verify RED**

Run: `npx vitest run tests/ui-project-transfer.test.tsx`

Expected: FAIL because the controls and modal do not exist.

- [ ] **Step 4: Add imports, state, and handlers to `main.tsx`**

Import Mantine `FileInput`, Lucide `Download` and `Upload`, the `ProjectBundle` type, and the Task 6 helpers. Add:

```ts
type ProjectImportState = {
  bundle: ProjectBundle | null;
  error: string;
  isSubmitting: boolean;
};

const emptyProjectImportState = (): ProjectImportState => ({
  bundle: null,
  error: "",
  isSubmitting: false,
});
```

Add component state for `projectImport`, `notice`, and handlers that:

- open the modal with empty state;
- call `file.text()` and `parseProjectBundleText` after selection;
- fetch `/api/projects/:id/export`, surface non-2xx JSON errors, and call `downloadResponse`;
- compute `projectBundlePreview(projectImport.bundle, state.projects)`;
- POST `{ bundle, replaceExisting: Boolean(preview.existingProjectId) }`;
- keep modal errors local, disable submission while pending, call `loadState`, close the modal, and set `notice` after success.

Use this explicit error check for raw export responses:

```ts
if (!response.ok) {
  const body = (await response.json().catch(() => ({}))) as { error?: string };
  throw new Error(body.error ?? `Request failed: ${response.status}`);
}
```

- [ ] **Step 5: Render project controls and status**

Beside **Add Project**, add an **Import Project** button with the Upload icon. On each project card, add an Export button that calls `event.preventDefault()` and `event.stopPropagation()` before exporting so it does not navigate into the project.

Render `notice` as a green Mantine `Alert` adjacent to the existing red error alert. Clear stale notice/error state when starting another transfer.

- [ ] **Step 6: Render the import modal**

Add a centered modal with:

- a `.json` `FileInput` labeled **Project JSON file**;
- local file/validation error text;
- project title/key and task/card/document count badges after parsing;
- a red warning when `existingProjectId` is present;
- **Cancel** and submit buttons;
- submit text/color `Replace Project`/red for an existing key, otherwise `Import Project`/teal;
- disabled and loading state while submitting.

The warning text must state: `This will replace all current project data for <KEY>. API token access to the destination project will be retained.`

- [ ] **Step 7: Run UI tests and the client suite**

Run: `npx vitest run tests/ui-project-transfer.test.tsx tests/project-transfer-utils.test.ts tests/ui-auth-token.test.tsx && npm run typecheck`

Expected: all named tests PASS and both TypeScript projects exit 0.

- [ ] **Step 8: Commit the browser workflow**

```bash
git add src/client/main.tsx tests/ui-project-transfer.test.tsx
git commit -m "Add project import export UI"
```

### Task 8: Document and verify the completed feature

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update user and API documentation**

Add these endpoints to the README API list:

```md
- `GET /api/projects/:id/export`
- `POST /api/projects/import`
```

Add a **Project Import and Export** section explaining that admins export from a project card, import from the Projects page, and must confirm replacement when a key already exists. State that bundles include tasks, dependencies, cards, readiness history, and documents, while API tokens and global activity are not copied. State the 10 MiB limit and that replacement retains the destination project's token assignments.

- [ ] **Step 2: Run formatting checks**

Run: `npx biome check src/shared/projectBundle.ts src/shared/types.ts src/server/store.ts src/server/storage.ts src/server/app.ts src/client/projectTransfer.ts src/client/main.tsx tests/project-bundle.test.ts tests/project-transfer.test.ts tests/api.test.ts tests/project-transfer-utils.test.ts tests/ui-project-transfer.test.tsx`

Expected: exit 0. If Biome reports formatting-only changes, run the same command with `--write`, inspect the diff, then rerun without `--write`.

- [ ] **Step 3: Run the complete test suite**

Run: `npm test`

Expected: exit 0 with all tests passing and no unhandled errors.

- [ ] **Step 4: Run both typechecks**

Run: `npm run typecheck`

Expected: exit 0.

- [ ] **Step 5: Build production client and server assets**

Run: `npm run build`

Expected: exit 0 with Vite client assets and TypeScript server output generated.

- [ ] **Step 6: Review the final diff against the design**

Run:

```bash
git diff --check
git status --short
git diff --stat HEAD~5..HEAD
```

Confirm the diff contains the shared contract, transactional store behavior, admin REST endpoints, browser controls/modal, tests, and README documentation. Confirm it does not modify MCP tools, token export data, whole-board importer behavior, or unrelated UI.

- [ ] **Step 7: Commit documentation or verification-only formatting**

```bash
git add README.md
git commit -m "Document project data transfer"
```

If formatting changed tracked source/test files in Step 2, include those exact files in this final commit after reviewing that the changes are mechanical only.
