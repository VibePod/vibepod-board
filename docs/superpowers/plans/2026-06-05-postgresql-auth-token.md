# PostgreSQL Auth Token Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JSON file storage with PostgreSQL, add env-configured admin authentication, and add admin-managed bearer tokens scoped to existing projects for REST and MCP access.

**Architecture:** Introduce an async store interface implemented by PostgreSQL, keep Express and MCP as transport layers, and pass an authorization context into store calls for project scoping. Admin sessions use HTTP-only cookies backed by an in-memory session manager; API/MCP tokens are generated once, stored as hashes, and mapped to projects in PostgreSQL.

**Tech Stack:** Node 22, TypeScript, Express 5, React 18, Mantine, MCP SDK, PostgreSQL, `pg`, Vitest, Supertest, Testing Library for focused React auth/token UI tests.

---

## File Structure

Create:

- `src/server/db.ts`: PostgreSQL pool creation and schema initialization.
- `src/server/store.ts`: async store interface, access-context types, and project-scope helpers.
- `src/server/auth.ts`: admin session auth, bearer token generation/hash helpers, Express auth middleware helpers.
- `src/server/jsonImport.ts`: pure JSON normalization and import functions.
- `src/server/import-json.ts`: CLI entry point for `npm run import:json`.
- `src/server/mcpTools.ts`: pure MCP tool handlers that accept a store and access context.
- `tests/helpers/postgres.ts`: PostgreSQL test pool, schema reset, and lifecycle helpers.
- `tests/helpers/store.ts`: `PostgresBoardStore` factory for tests.
- `tests/postgres-schema.test.ts`: database schema bootstrap tests.
- `tests/access.test.ts`: project-scope helper tests.
- `tests/auth.test.ts`: admin session and token helper tests.
- `tests/token-store.test.ts`: token persistence tests.
- `tests/json-import.test.ts`: one-shot JSON import tests.
- `tests/mcp-auth.test.ts`: MCP route auth and scoped handler tests.
- `tests/ui-auth-token.test.tsx`: focused React tests for login and token-management rendering.
- `tests/token-config-utils.test.ts`: MCP token config example tests.

Modify:

- `package.json`: add `pg`, test UI dependencies, and `import:json` script.
- `package-lock.json`: update through `npm install`.
- `.github/workflows/ci.yml`: add PostgreSQL service and `TEST_DATABASE_URL`.
- `compose.yml`: add `postgres` service, database volume, and `DATABASE_URL` for board.
- `.env.example`: add database and admin variables; remove runtime `DATA_DIR`.
- `Dockerfile`: remove `/data` runtime volume setup.
- `README.md`: document PostgreSQL runtime, admin env, token setup, and import command.
- `docs/integration-guide.md`: document bearer token MCP configuration.
- `src/shared/types.ts`: add token/admin response types.
- `src/shared/integrationExamples.ts`: add token-aware MCP config examples.
- `src/server/storage.ts`: replace JSON-backed `BoardStore` with `PostgresBoardStore`.
- `src/server/app.ts`: make handlers async, add auth endpoints, protect routes, and enforce project token scoping.
- `src/server/mcp.ts`: require bearer tokens and register scoped MCP handlers.
- `src/server/index.ts`: create pool, initialize schema, validate admin env, and start app after async setup.
- `src/client/main.tsx`: add login flow, token-management modal/view, logout, and token-aware MCP copy snippets.
- `src/client/styles.css`: add login and token-management styles using the existing visual language.
- `tests/storage.test.ts`: convert existing storage behavior tests to async PostgreSQL store tests.
- `tests/api.test.ts`: update existing API tests for admin auth and add token-scope cases.
- `tests/compose.test.ts`: assert PostgreSQL service and removed runtime board data volume.
- `tests/integration-examples.test.ts`: assert bearer token headers appear in MCP examples.

Delete:

- Runtime use of `/data/board.json` from server startup and Docker runtime.

---

### Task 1: Add PostgreSQL and UI Test Dependencies

**Files:**

- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `.github/workflows/ci.yml`
- Create: `tests/helpers/postgres.ts`

- [ ] **Step 1: Install dependencies**

Run:

```bash
npm install pg
npm install -D @types/pg @testing-library/react @testing-library/user-event jsdom
```

Expected: `package.json` and `package-lock.json` include `pg`, `@types/pg`, `@testing-library/react`, `@testing-library/user-event`, and `jsdom`.

- [ ] **Step 2: Add the PostgreSQL test helper**

Create `tests/helpers/postgres.ts`:

```ts
import { Pool } from "pg";

export const testDatabaseUrl =
  process.env.TEST_DATABASE_URL ??
  "postgres://vibepod:vibepod@localhost:5432/vibepod_board_test";

export const createTestPool = () =>
  new Pool({
    connectionString: testDatabaseUrl,
    max: 1
  });

export const resetDatabase = async (pool: Pool) => {
  await pool.query("DROP SCHEMA IF EXISTS public CASCADE");
  await pool.query("CREATE SCHEMA public");
};
```

- [ ] **Step 3: Add PostgreSQL to CI**

Modify `.github/workflows/ci.yml` so the `test` job contains:

```yaml
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: vibepod_board_test
          POSTGRES_USER: vibepod
          POSTGRES_PASSWORD: vibepod
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U vibepod -d vibepod_board_test"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
```

Add this environment value to each `npm test`, `npm run typecheck`, and `npm run build` step or to the job:

```yaml
    env:
      TEST_DATABASE_URL: postgres://vibepod:vibepod@localhost:5432/vibepod_board_test
      DATABASE_URL: postgres://vibepod:vibepod@localhost:5432/vibepod_board_test
      ADMIN_USERNAME: admin
      ADMIN_PASSWORD: admin-password
```

- [ ] **Step 4: Verify dependency installation**

Run:

```bash
node -e "import('pg').then(() => console.log('pg ok'))"
node -e "import('@testing-library/react').then(() => console.log('testing-library ok'))"
```

Expected:

```text
pg ok
testing-library ok
```

- [ ] **Step 5: Commit**

```bash
git add package.json package-lock.json .github/workflows/ci.yml tests/helpers/postgres.ts
git commit -m "test: add postgres test harness"
```

---

### Task 2: Add Database Schema Initialization

**Files:**

- Create: `src/server/db.ts`
- Create: `tests/postgres-schema.test.ts`

- [ ] **Step 1: Write the failing schema test**

Create `tests/postgres-schema.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { Pool } from "pg";

import { createPool, initializeDatabase } from "../src/server/db.js";
import { createTestPool, resetDatabase, testDatabaseUrl } from "./helpers/postgres.js";

let pool: Pool;

beforeEach(async () => {
  pool = createTestPool();
  await resetDatabase(pool);
});

afterEach(async () => {
  await pool.end();
});

describe("PostgreSQL schema", () => {
  it("creates board and token tables", async () => {
    await initializeDatabase(pool);

    const tables = await pool.query<{ table_name: string }>(
      "select table_name from information_schema.tables where table_schema = 'public' order by table_name"
    );

    expect(tables.rows.map((row) => row.table_name)).toEqual([
      "activity_events",
      "api_token_projects",
      "api_tokens",
      "board_cards",
      "documents",
      "ideas",
      "projects"
    ]);
  });

  it("creates a pool from a database URL", async () => {
    const created = createPool(testDatabaseUrl);
    try {
      expect(created.options.connectionString).toBe(testDatabaseUrl);
    } finally {
      await created.end();
    }
  });
});
```

- [ ] **Step 2: Run the schema test and verify RED**

Run:

```bash
npm test -- tests/postgres-schema.test.ts
```

Expected: FAIL because `src/server/db.ts` does not exist.

- [ ] **Step 3: Implement schema initialization**

Create `src/server/db.ts`:

```ts
import { Pool } from "pg";

export const createPool = (connectionString: string) =>
  new Pool({
    connectionString,
    max: Number(process.env.DATABASE_POOL_MAX ?? 10)
  });

const schemaSql = `
create table if not exists projects (
  id text primary key,
  key text not null unique,
  title text not null,
  summary text not null default '',
  created_at timestamptz not null,
  updated_at timestamptz not null
);

create table if not exists ideas (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  task_number integer not null,
  title text not null,
  summary text not null default '',
  details text not null default '',
  status text not null,
  labels jsonb not null default '[]'::jsonb,
  acceptance_criteria jsonb not null default '[]'::jsonb,
  github_issue_url text,
  github_issue_number integer,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  unique(project_id, task_number)
);

create table if not exists board_cards (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  idea_id text references ideas(id) on delete set null,
  title text not null,
  details text not null default '',
  column_name text not null,
  github_issue_url text,
  github_issue_number integer,
  labels jsonb not null default '[]'::jsonb,
  created_at timestamptz not null,
  updated_at timestamptz not null
);

create table if not exists documents (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  title text not null,
  kind text not null,
  content text not null default '',
  linked_idea_ids jsonb not null default '[]'::jsonb,
  linked_card_ids jsonb not null default '[]'::jsonb,
  created_at timestamptz not null,
  updated_at timestamptz not null
);

create table if not exists activity_events (
  id text primary key,
  type text not null,
  message text not null,
  created_at timestamptz not null
);

create table if not exists api_tokens (
  id text primary key,
  name text not null,
  token_hash text not null unique,
  created_at timestamptz not null,
  last_used_at timestamptz,
  revoked_at timestamptz
);

create table if not exists api_token_projects (
  token_id text not null references api_tokens(id) on delete cascade,
  project_id text not null references projects(id) on delete cascade,
  primary key (token_id, project_id)
);

create index if not exists ideas_project_updated_idx on ideas(project_id, updated_at desc);
create index if not exists board_cards_project_updated_idx on board_cards(project_id, updated_at desc);
create index if not exists documents_project_updated_idx on documents(project_id, updated_at desc);
create index if not exists activity_events_created_idx on activity_events(created_at desc);
`;

export const initializeDatabase = async (pool: Pick<Pool, "query">) => {
  await pool.query(schemaSql);
};
```

- [ ] **Step 4: Run the schema test and verify GREEN**

Run:

```bash
npm test -- tests/postgres-schema.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/db.ts tests/postgres-schema.test.ts
git commit -m "feat: initialize postgres schema"
```

---

### Task 3: Add Store Interface and Project Scope Helpers

**Files:**

- Create: `src/server/store.ts`
- Create: `tests/access.test.ts`
- Modify: `src/shared/types.ts`

- [ ] **Step 1: Write the failing access-helper test**

Create `tests/access.test.ts`:

```ts
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
```

- [ ] **Step 2: Run the access test and verify RED**

Run:

```bash
npm test -- tests/access.test.ts
```

Expected: FAIL because `src/server/store.ts` does not exist.

- [ ] **Step 3: Add shared token response types**

Append these exports to `src/shared/types.ts`:

```ts
export type ApiTokenProject = Pick<Project, "id" | "key" | "title">;

export type ApiTokenSummary = {
  id: string;
  name: string;
  projects: ApiTokenProject[];
  createdAt: string;
  lastUsedAt?: string;
  revokedAt?: string;
};

export type CreateApiTokenInput = {
  name: string;
  projectIds: string[];
};

export type UpdateApiTokenInput = {
  name?: string;
  projectIds?: string[];
};

export type CreatedApiTokenResponse = {
  item: ApiTokenSummary;
  token: string;
};

export type AuthMeResponse = {
  authenticated: boolean;
  username?: string;
};
```

- [ ] **Step 4: Implement the store interface and helpers**

Create `src/server/store.ts`:

```ts
import type {
  ApiTokenSummary,
  BoardCard,
  BoardColumn,
  BoardColumns,
  BoardData,
  CreateApiTokenInput,
  CreateBoardCardOptions,
  CreateDocumentInput,
  CreateIdeaInput,
  CreateProjectInput,
  Idea,
  PlanDocument,
  Project,
  UpdateApiTokenInput,
  UpdateDocumentInput,
  UpdateIdeaInput,
  UpdateProjectInput
} from "../shared/types.js";

export type AdminAccess = {
  kind: "admin";
  username: string;
};

export type TokenAccess = {
  kind: "token";
  tokenId: string;
  projectIds: string[];
};

export type AccessContext = AdminAccess | TokenAccess;

export type AuthenticatedToken = {
  tokenId: string;
  projectIds: string[];
};

export type CreatedApiToken = {
  item: ApiTokenSummary;
  token: string;
};

export interface BoardDataStore {
  getState(access: AccessContext): Promise<BoardData>;
  listProjects(access: AccessContext): Promise<Project[]>;
  createProject(input: CreateProjectInput): Promise<Project>;
  updateProject(id: string, input: UpdateProjectInput): Promise<Project>;
  listIdeas(access: AccessContext, projectId?: string): Promise<Idea[]>;
  createIdea(access: AccessContext, input: CreateIdeaInput): Promise<Idea>;
  updateIdea(access: AccessContext, id: string, input: UpdateIdeaInput): Promise<Idea>;
  markIdeaReady(access: AccessContext, id: string): Promise<Idea>;
  setIdeaBoardAvailability(access: AccessContext, id: string, available: boolean): Promise<Idea>;
  createBoardCardFromIdea(access: AccessContext, id: string, options: CreateBoardCardOptions): Promise<BoardCard>;
  listBoardCards(access: AccessContext, projectId?: string): Promise<BoardCard[]>;
  getBoardColumns(access: AccessContext, projectId?: string): Promise<BoardColumns>;
  moveBoardCard(access: AccessContext, id: string, column: BoardColumn): Promise<BoardCard>;
  listDocuments(access: AccessContext, projectId?: string): Promise<PlanDocument[]>;
  createDocument(access: AccessContext, input: CreateDocumentInput): Promise<PlanDocument>;
  updateDocument(access: AccessContext, id: string, input: UpdateDocumentInput): Promise<PlanDocument>;
  listActivity(access: AccessContext): Promise<BoardData["activity"]>;
  listApiTokens(): Promise<ApiTokenSummary[]>;
  createApiToken(input: CreateApiTokenInput): Promise<CreatedApiToken>;
  updateApiToken(id: string, input: UpdateApiTokenInput): Promise<ApiTokenSummary>;
  revokeApiToken(id: string): Promise<ApiTokenSummary>;
  authenticateApiToken(token: string): Promise<AuthenticatedToken | null>;
}

export const adminAccess = (username: string): AdminAccess => ({ kind: "admin", username });

export const tokenAccess = (tokenId: string, projectIds: string[]): TokenAccess => ({
  kind: "token",
  tokenId,
  projectIds
});

export const isAdminAccess = (access: AccessContext): access is AdminAccess => access.kind === "admin";

export const assertCanAccessProject = (access: AccessContext, projectId: string) => {
  if (access.kind === "admin" || access.projectIds.includes(projectId)) {
    return;
  }
  throw new Error(`Token is not allowed to access project: ${projectId}`);
};

export const filterProjectIds = (access: AccessContext, projectIds: string[]): string[] =>
  access.kind === "admin" ? projectIds : projectIds.filter((projectId) => access.projectIds.includes(projectId));

export const defaultProjectIdForCreate = (access: AccessContext, requestedProjectId: string | undefined): string | undefined => {
  if (access.kind === "admin") {
    return requestedProjectId;
  }
  if (requestedProjectId) {
    assertCanAccessProject(access, requestedProjectId);
    return requestedProjectId;
  }
  const [firstProjectId] = access.projectIds;
  if (!firstProjectId) {
    throw new Error("Token is not mapped to any projects");
  }
  return firstProjectId;
};
```

- [ ] **Step 5: Run the access test and typecheck**

Run:

```bash
npm test -- tests/access.test.ts
npm run typecheck
```

Expected: both commands PASS.

- [ ] **Step 6: Commit**

```bash
git add src/server/store.ts src/shared/types.ts tests/access.test.ts
git commit -m "feat: add store access contract"
```

---

### Task 4: Implement PostgreSQL Store for Projects and Ideas

**Files:**

- Modify: `src/server/storage.ts`
- Create: `tests/helpers/store.ts`
- Modify: `tests/storage.test.ts`

- [ ] **Step 1: Add the test store helper**

Create `tests/helpers/store.ts`:

```ts
import type { Pool } from "pg";

import { initializeDatabase } from "../../src/server/db.js";
import { PostgresBoardStore } from "../../src/server/storage.js";
import { createTestPool, resetDatabase } from "./postgres.js";

export const createTestStore = async () => {
  const pool = createTestPool();
  await resetDatabase(pool);
  await initializeDatabase(pool);
  const store = new PostgresBoardStore(pool);
  return { pool, store };
};

export const closeTestPool = async (pool: Pool) => {
  await pool.end();
};
```

- [ ] **Step 2: Convert project and idea storage tests to async PostgreSQL tests**

In `tests/storage.test.ts`, replace the temp-file setup with:

```ts
import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { adminAccess } from "../src/server/store.js";
import type { PostgresBoardStore } from "../src/server/storage.js";
import { closeTestPool, createTestStore } from "./helpers/store.js";

let pool: Pool;
let store: PostgresBoardStore;
const admin = adminAccess("admin");

beforeEach(async () => {
  const created = await createTestStore();
  pool = created.pool;
  store = created.store;
});

afterEach(async () => {
  await closeTestPool(pool);
});
```

Keep these existing test names in this task and convert each store call to `await`:

```ts
it("requires unique 1 to 3 capital letter project IDs", async () => {});
it("assigns incrementing task numbers per project", async () => {});
it("stores tasks, board cards, and documents inside a project", async () => {});
it("stores denied tasks for rejected ideas", async () => {});
it("refines an idea, marks it ready, and promotes it to a board card", async () => {});
```

For project-scoped methods, pass `admin` as the first argument:

```ts
const idea = await store.createIdea(admin, { projectId: project.id, title: "First app task" });
expect((await store.listIdeas(admin, project.id))[0].id).toBe(idea.id);
```

- [ ] **Step 3: Run the converted storage tests and verify RED**

Run:

```bash
npm test -- tests/storage.test.ts
```

Expected: FAIL because `PostgresBoardStore` does not exist and `storage.ts` still exports the JSON-backed `BoardStore`.

- [ ] **Step 4: Replace `BoardStore` with a PostgreSQL-backed class for projects and ideas**

In `src/server/storage.ts`, export:

```ts
export class PostgresBoardStore implements BoardDataStore {
  constructor(private readonly pool: Pool) {}
}
```

Implement these methods first:

- `listProjects`
- `createProject`
- `updateProject`
- `listIdeas`
- `createIdea`
- `updateIdea`

Keep the legacy JSON normalization helper functions in `storage.ts` during this task. Task 6 moves that logic into `jsonImport.ts`; deleting it before Task 6 makes the import behavior harder to preserve.

Use the existing validation behavior and messages from the JSON-backed store:

```ts
const projectKeyPattern = /^[A-Z]{1,3}$/;

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
```

Use `randomUUID()` for IDs, `new Date().toISOString()` for API timestamps, and PostgreSQL transactions for methods that insert activity events with domain rows.

For `createIdea`, resolve the project with:

```ts
const projectId = defaultProjectIdForCreate(access, input.projectId);
```

When `projectId` is missing for an admin request, preserve the current behavior by creating or reusing the first project named `General` with key `GEN`.

- [ ] **Step 5: Run storage tests and fix only project/idea failures**

Run:

```bash
npm test -- tests/storage.test.ts
```

Expected: the project and idea tests listed in Step 2 PASS. Tests that still depend on board cards, documents, legacy JSON migration, and full state export can fail until Task 5 and Task 6.

- [ ] **Step 6: Commit**

```bash
git add src/server/storage.ts tests/helpers/store.ts tests/storage.test.ts
git commit -m "feat: persist projects and ideas in postgres"
```

---

### Task 5: Complete PostgreSQL Store for Cards, Documents, Activity, and State Export

**Files:**

- Modify: `src/server/storage.ts`
- Modify: `tests/storage.test.ts`

- [ ] **Step 1: Convert the remaining non-import storage tests**

In `tests/storage.test.ts`, keep these test names and convert all store calls to `await` with `admin`:

```ts
it("uses ready as the flag for board availability", async () => {});
it("moves board cards between any columns without workflow constraints", async () => {});
it("stores execution plan documents linked to ideas and board cards", async () => {});
```

Add a focused state-export assertion:

```ts
it("exports scoped state for admins and project tokens", async () => {
  const project = await store.createProject({ title: "Launch site", key: "LS" });
  const otherProject = await store.createProject({ title: "Backlog", key: "BL" });
  await store.createIdea(admin, { projectId: project.id, title: "Scoped task" });
  await store.createIdea(admin, { projectId: otherProject.id, title: "Hidden task" });

  const fullState = await store.getState(admin);
  const scopedState = await store.getState({ kind: "token", tokenId: "token-1", projectIds: [project.id] });

  expect(fullState.projects.map((item) => item.id).sort()).toEqual([otherProject.id, project.id].sort());
  expect(scopedState.projects.map((item) => item.id)).toEqual([project.id]);
  expect(scopedState.ideas.map((item) => item.title)).toEqual(["Scoped task"]);
});
```

- [ ] **Step 2: Run storage tests and verify RED**

Run:

```bash
npm test -- tests/storage.test.ts
```

Expected: FAIL because card, document, activity, and state export methods are incomplete.

- [ ] **Step 3: Implement card, document, activity, and state methods**

Implement these `PostgresBoardStore` methods:

- `getState`
- `markIdeaReady`
- `setIdeaBoardAvailability`
- `createBoardCardFromIdea`
- `listBoardCards`
- `getBoardColumns`
- `moveBoardCard`
- `listDocuments`
- `createDocument`
- `updateDocument`
- `listActivity`

Keep the current behavior:

- `markIdeaReady` creates a `ready` board card if none exists.
- `setIdeaBoardAvailability(access, id, false)` removes the board card for the idea and restores status to `refining` when details or acceptance criteria exist, otherwise `idea`.
- `moveBoardCard` allows movement to any `BoardColumn`.
- `createDocument` defaults `kind` to `execution_plan`.
- Activity is capped by deleting rows outside the most recent 100:

```sql
delete from activity_events
where id not in (
  select id from activity_events
  order by created_at desc
  limit 100
)
```

Convert database rows to API types with helpers inside `storage.ts`:

```ts
const rowTimestamp = (value: Date | string): string =>
  value instanceof Date ? value.toISOString() : new Date(value).toISOString();

const jsonStringArray = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
```

- [ ] **Step 4: Run storage tests and verify GREEN**

Run:

```bash
npm test -- tests/storage.test.ts
```

Expected: PASS for all non-import storage tests.

- [ ] **Step 5: Commit**

```bash
git add src/server/storage.ts tests/storage.test.ts
git commit -m "feat: complete postgres board storage"
```

---

### Task 6: Add JSON Normalization and One-Shot Import

**Files:**

- Create: `src/server/jsonImport.ts`
- Create: `src/server/import-json.ts`
- Modify: `src/server/storage.ts`
- Modify: `package.json`
- Create: `tests/json-import.test.ts`
- Modify: `tests/storage.test.ts`

- [ ] **Step 1: Move legacy JSON migration coverage to import tests**

Create `tests/json-import.test.ts`:

```ts
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { adminAccess } from "../src/server/store.js";
import { importBoardJsonFile, normalizeImportedBoardData } from "../src/server/jsonImport.js";
import { PostgresBoardStore } from "../src/server/storage.js";
import { closeTestPool, createTestStore } from "./helpers/store.js";

let tempDir: string;
let pool: Pool;
let store: PostgresBoardStore;
const admin = adminAccess("admin");

beforeEach(async () => {
  tempDir = await mkdtemp(join(tmpdir(), "vibepod-board-import-"));
  const created = await createTestStore();
  pool = created.pool;
  store = created.store;
});

afterEach(async () => {
  await closeTestPool(pool);
  await rm(tempDir, { recursive: true, force: true });
});

describe("JSON import", () => {
  it("normalizes legacy unowned work into a default project", () => {
    const timestamp = "2026-01-01T00:00:00.000Z";
    const normalized = normalizeImportedBoardData({
      schemaVersion: 1,
      ideas: [
        {
          id: "idea-1",
          title: "Legacy task",
          summary: "",
          details: "",
          status: "idea",
          labels: [],
          acceptanceCriteria: [],
          createdAt: timestamp,
          updatedAt: timestamp
        }
      ],
      boardCards: [
        {
          id: "card-1",
          title: "Legacy card",
          details: "",
          column: "ready",
          ideaId: "idea-1",
          labels: [],
          createdAt: timestamp,
          updatedAt: timestamp
        }
      ],
      documents: [
        {
          id: "document-1",
          title: "Legacy note",
          kind: "notes",
          content: "",
          linkedIdeaIds: ["idea-1"],
          linkedCardIds: ["card-1"],
          createdAt: timestamp,
          updatedAt: timestamp
        }
      ],
      activity: []
    });

    expect(normalized.schemaVersion).toBe(3);
    expect(normalized.projects[0].title).toBe("General");
    expect(normalized.ideas[0].projectId).toBe(normalized.projects[0].id);
    expect(normalized.ideas[0].taskNumber).toBe(1);
    expect(normalized.boardCards[0].projectId).toBe(normalized.projects[0].id);
    expect(normalized.documents[0].projectId).toBe(normalized.projects[0].id);
  });

  it("imports JSON into an empty PostgreSQL database", async () => {
    const timestamp = "2026-01-01T00:00:00.000Z";
    const filePath = join(tempDir, "board.json");
    await writeFile(
      filePath,
      `${JSON.stringify({
        schemaVersion: 3,
        projects: [
          {
            id: "project-1",
            key: "LS",
            title: "Launch site",
            summary: "Coordinate launch",
            createdAt: timestamp,
            updatedAt: timestamp
          }
        ],
        ideas: [
          {
            id: "idea-1",
            projectId: "project-1",
            taskNumber: 1,
            title: "Publish",
            summary: "",
            details: "",
            status: "ready",
            labels: ["launch"],
            acceptanceCriteria: [],
            createdAt: timestamp,
            updatedAt: timestamp
          }
        ],
        boardCards: [],
        documents: [],
        activity: []
      })}\n`,
      "utf8"
    );

    await importBoardJsonFile(pool, filePath);

    expect((await store.listProjects(admin))[0].id).toBe("project-1");
    expect((await store.listIdeas(admin, "project-1"))[0].title).toBe("Publish");
  });

  it("refuses to import over existing board data", async () => {
    await store.createProject({ key: "EX", title: "Existing" });
    const filePath = join(tempDir, "empty-board.json");
    await writeFile(filePath, JSON.stringify({ schemaVersion: 3 }), "utf8");

    await expect(importBoardJsonFile(pool, filePath)).rejects.toThrow(
      "PostgreSQL board tables are not empty"
    );
  });
});
```

- [ ] **Step 2: Run import tests and verify RED**

Run:

```bash
npm test -- tests/json-import.test.ts
```

Expected: FAIL because `jsonImport.ts` does not exist.

- [ ] **Step 3: Implement JSON normalization**

Create `src/server/jsonImport.ts` by moving the pure legacy normalization logic out of the old JSON `BoardStore`:

- `emptyData`
- `normalizeProjects`
- `migrateProjectOwnership`
- `normalizeIdeaTaskNumbers`
- `normalizeIdeaStatuses`
- `nextAvailableProjectKey`
- `projectKeyFromTitle`

Export these functions:

```ts
export function normalizeImportedBoardData(input: PersistedBoardData): BoardData;
export function importBoardJsonFile(
  pool: Pool,
  filePath: string,
  options?: { allowNonEmpty?: boolean }
): Promise<void>;
```

`importBoardJsonFile` must:

1. Read the JSON file.
2. Normalize to schema version 3.
3. Check that `projects`, `ideas`, `board_cards`, `documents`, and `activity_events` are empty unless `allowNonEmpty` is `true`.
4. Insert rows in a transaction in this order: projects, ideas, board cards, documents, activity.
5. Preserve IDs and timestamps.

- [ ] **Step 4: Add the CLI entry point and npm script**

Create `src/server/import-json.ts`:

```ts
import { createPool, initializeDatabase } from "./db.js";
import { importBoardJsonFile } from "./jsonImport.js";

const filePath = process.argv[2];

if (!filePath) {
  console.error("Usage: npm run import:json -- ./data/board.json");
  process.exit(1);
}

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) {
  console.error("DATABASE_URL is required");
  process.exit(1);
}

const pool = createPool(databaseUrl);

try {
  await initializeDatabase(pool);
  await importBoardJsonFile(pool, filePath);
  console.log(`Imported board JSON from ${filePath}`);
} finally {
  await pool.end();
}
```

Add to `package.json`:

```json
"import:json": "tsx src/server/import-json.ts"
```

- [ ] **Step 5: Remove legacy migration tests from `tests/storage.test.ts`**

Delete these old file-storage tests from `tests/storage.test.ts` because `tests/json-import.test.ts` now covers them:

- `migrates existing work into a default project`
- `migrates legacy synced tasks to ready status`

- [ ] **Step 6: Run import and storage tests**

Run:

```bash
npm test -- tests/json-import.test.ts tests/storage.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/server/jsonImport.ts src/server/import-json.ts src/server/storage.ts package.json package-lock.json tests/json-import.test.ts tests/storage.test.ts
git commit -m "feat: import legacy board json into postgres"
```

---

### Task 7: Add Admin Sessions and Token Persistence

**Files:**

- Create: `src/server/auth.ts`
- Modify: `src/server/storage.ts`
- Create: `tests/auth.test.ts`
- Create: `tests/token-store.test.ts`

- [ ] **Step 1: Write auth helper tests**

Create `tests/auth.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import {
  createAdminSessionManager,
  createRawApiToken,
  hashApiToken,
  parseBearerToken
} from "../src/server/auth.js";

describe("auth helpers", () => {
  it("creates and validates admin sessions", () => {
    const sessions = createAdminSessionManager({
      username: "admin",
      password: "secret"
    });

    expect(sessions.login("admin", "wrong")).toBeNull();
    const session = sessions.login("admin", "secret");

    expect(session?.username).toBe("admin");
    expect(session?.cookie).toContain("vibepod_session=");
    expect(sessions.authenticateCookie(session?.cookie ?? "")?.username).toBe("admin");
    sessions.logout(session?.id ?? "");
    expect(sessions.authenticateCookie(session?.cookie ?? "")).toBeNull();
  });

  it("generates hashable bearer tokens", () => {
    const token = createRawApiToken();

    expect(token).toMatch(/^vbp_[A-Za-z0-9_-]{43}$/);
    expect(hashApiToken(token)).toHaveLength(64);
    expect(hashApiToken(token)).toBe(hashApiToken(token));
  });

  it("parses bearer tokens", () => {
    expect(parseBearerToken("Bearer vbp_abc")).toBe("vbp_abc");
    expect(parseBearerToken("Basic abc")).toBeNull();
    expect(parseBearerToken(undefined)).toBeNull();
  });
});
```

- [ ] **Step 2: Run auth tests and verify RED**

Run:

```bash
npm test -- tests/auth.test.ts
```

Expected: FAIL because `auth.ts` does not exist.

- [ ] **Step 3: Implement admin session and token helpers**

Create `src/server/auth.ts`:

```ts
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import type { Request, Response } from "express";

type AdminCredentials = {
  username: string;
  password: string;
};

type AdminSession = {
  id: string;
  username: string;
  createdAt: number;
};

const sessionCookieName = "vibepod_session";

export const createRawApiToken = () => `vbp_${randomBytes(32).toString("base64url")}`;

export const hashApiToken = (token: string) =>
  createHash("sha256").update(token, "utf8").digest("hex");

export const parseBearerToken = (authorization: string | undefined): string | null => {
  if (!authorization?.startsWith("Bearer ")) {
    return null;
  }
  const token = authorization.slice("Bearer ".length).trim();
  return token || null;
};

export const createAdminSessionManager = (credentials: AdminCredentials) => {
  const sessions = new Map<string, AdminSession>();

  const matches = (actual: string, expected: string) => {
    const actualBuffer = Buffer.from(actual);
    const expectedBuffer = Buffer.from(expected);
    return actualBuffer.length === expectedBuffer.length && timingSafeEqual(actualBuffer, expectedBuffer);
  };

  return {
    login(username: string, password: string) {
      if (!matches(username, credentials.username) || !matches(password, credentials.password)) {
        return null;
      }
      const id = randomBytes(32).toString("base64url");
      const session = { id, username, createdAt: Date.now() };
      sessions.set(id, session);
      return {
        id,
        username,
        cookie: `${sessionCookieName}=${id}; HttpOnly; SameSite=Lax; Path=/`
      };
    },
    authenticateCookie(cookieHeader: string | undefined) {
      const id = cookieHeader
        ?.split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith(`${sessionCookieName}=`))
        ?.slice(sessionCookieName.length + 1);
      return id ? sessions.get(id) ?? null : null;
    },
    logout(id: string) {
      sessions.delete(id);
    },
    clearCookie: `${sessionCookieName}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0`
  };
};

export type AdminSessionManager = ReturnType<typeof createAdminSessionManager>;

export const setSessionCookie = (res: Response, cookie: string) => {
  res.setHeader("Set-Cookie", cookie);
};

export const sessionFromRequest = (req: Request, sessions: AdminSessionManager) =>
  sessions.authenticateCookie(req.headers.cookie);
```

- [ ] **Step 4: Run auth tests and verify GREEN**

Run:

```bash
npm test -- tests/auth.test.ts
```

Expected: PASS.

- [ ] **Step 5: Write token store tests**

Create `tests/token-store.test.ts`:

```ts
import type { Pool } from "pg";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { adminAccess } from "../src/server/store.js";
import type { PostgresBoardStore } from "../src/server/storage.js";
import { closeTestPool, createTestStore } from "./helpers/store.js";

let pool: Pool;
let store: PostgresBoardStore;
const admin = adminAccess("admin");

beforeEach(async () => {
  const created = await createTestStore();
  pool = created.pool;
  store = created.store;
});

afterEach(async () => {
  await closeTestPool(pool);
});

describe("API token storage", () => {
  it("creates tokens mapped to projects and stores only a hash", async () => {
    const project = await store.createProject({ key: "APP", title: "App" });

    const created = await store.createApiToken({
      name: "Codex",
      projectIds: [project.id]
    });

    expect(created.token).toMatch(/^vbp_/);
    expect(created.item.name).toBe("Codex");
    expect(created.item.projects).toEqual([{ id: project.id, key: "APP", title: "App" }]);

    const rawRows = await pool.query<{ token_hash: string }>("select token_hash from api_tokens");
    expect(rawRows.rows[0].token_hash).not.toBe(created.token);

    const authenticated = await store.authenticateApiToken(created.token);
    expect(authenticated).toEqual({ tokenId: created.item.id, projectIds: [project.id] });
  });

  it("updates project mappings and rejects revoked tokens", async () => {
    const app = await store.createProject({ key: "APP", title: "App" });
    const api = await store.createProject({ key: "API", title: "API" });
    const created = await store.createApiToken({ name: "Agent", projectIds: [app.id] });

    const updated = await store.updateApiToken(created.item.id, {
      name: "Agent updated",
      projectIds: [api.id]
    });
    expect(updated.name).toBe("Agent updated");
    expect(updated.projects.map((project) => project.id)).toEqual([api.id]);

    await store.revokeApiToken(created.item.id);
    expect(await store.authenticateApiToken(created.token)).toBeNull();
  });
});
```

- [ ] **Step 6: Run token store tests and verify RED**

Run:

```bash
npm test -- tests/token-store.test.ts
```

Expected: FAIL because token methods are not implemented.

- [ ] **Step 7: Implement token persistence in `PostgresBoardStore`**

Add these methods to `src/server/storage.ts`:

- `listApiTokens`
- `createApiToken`
- `updateApiToken`
- `revokeApiToken`
- `authenticateApiToken`

Use `createRawApiToken()` and `hashApiToken()` from `auth.ts`. Insert token-project mappings inside the same transaction as token creation or update. `authenticateApiToken` updates `last_used_at` only after confirming `revoked_at is null`.

- [ ] **Step 8: Run auth and token tests**

Run:

```bash
npm test -- tests/auth.test.ts tests/token-store.test.ts
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/server/auth.ts src/server/storage.ts tests/auth.test.ts tests/token-store.test.ts
git commit -m "feat: add admin sessions and api tokens"
```

---

### Task 8: Protect REST API and Enforce Project Token Scoping

**Files:**

- Modify: `src/server/app.ts`
- Modify: `tests/api.test.ts`

- [ ] **Step 1: Add REST auth tests**

At the top of `tests/api.test.ts`, switch to the PostgreSQL helper setup:

```ts
import type { Pool } from "pg";
import request from "supertest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createAdminSessionManager } from "../src/server/auth.js";
import { createApp } from "../src/server/app.js";
import type { PostgresBoardStore } from "../src/server/storage.js";
import { closeTestPool, createTestStore } from "./helpers/store.js";

let pool: Pool;
let store: PostgresBoardStore;

beforeEach(async () => {
  const created = await createTestStore();
  pool = created.pool;
  store = created.store;
});

afterEach(async () => {
  await closeTestPool(pool);
});

const createAuthedApp = () => {
  const sessions = createAdminSessionManager({ username: "admin", password: "secret" });
  const app = createApp({ store, sessions });
  return { app, sessions };
};
```

Add these tests before converting the existing API tests:

```ts
it("requires admin auth for REST board endpoints", async () => {
  const { app } = createAuthedApp();

  await request(app).get("/api/health").expect(200);
  await request(app).get("/api/projects").expect(401);

  const agent = request.agent(app);
  await agent.post("/api/auth/login").send({ username: "admin", password: "secret" }).expect(200);
  await agent.get("/api/projects").expect(200);
  await agent.post("/api/auth/logout").expect(200);
  await agent.get("/api/projects").expect(401);
});

it("allows project tokens to access only mapped project data", async () => {
  const { app } = createAuthedApp();
  const agent = request.agent(app);
  await agent.post("/api/auth/login").send({ username: "admin", password: "secret" }).expect(200);

  const appProject = await agent.post("/api/projects").send({ title: "App", key: "APP" }).expect(201);
  const apiProject = await agent.post("/api/projects").send({ title: "API", key: "API" }).expect(201);
  await agent.post("/api/ideas").send({ projectId: appProject.body.item.id, title: "Visible" }).expect(201);
  await agent.post("/api/ideas").send({ projectId: apiProject.body.item.id, title: "Hidden" }).expect(201);

  const tokenResponse = await agent
    .post("/api/tokens")
    .send({ name: "Codex", projectIds: [appProject.body.item.id] })
    .expect(201);
  const token = tokenResponse.body.token;

  const tokenProjects = await request(app)
    .get("/api/projects")
    .set("Authorization", `Bearer ${token}`)
    .expect(200);
  expect(tokenProjects.body.items.map((project: { id: string }) => project.id)).toEqual([
    appProject.body.item.id
  ]);

  const tokenIdeas = await request(app)
    .get("/api/ideas")
    .set("Authorization", `Bearer ${token}`)
    .expect(200);
  expect(tokenIdeas.body.items.map((idea: { title: string }) => idea.title)).toEqual(["Visible"]);

  await request(app)
    .post("/api/ideas")
    .set("Authorization", `Bearer ${token}`)
    .send({ projectId: apiProject.body.item.id, title: "Forbidden" })
    .expect(403);

  await request(app)
    .post("/api/projects")
    .set("Authorization", `Bearer ${token}`)
    .send({ key: "NEW", title: "Nope" })
    .expect(403);
});
```

- [ ] **Step 2: Run API tests and verify RED**

Run:

```bash
npm test -- tests/api.test.ts
```

Expected: FAIL because `createApp` has no `sessions` option and routes are not protected.

- [ ] **Step 3: Make `createApp` async-store aware and add auth middleware**

Modify `src/server/app.ts`:

```ts
type CreateAppOptions = {
  store: BoardDataStore;
  sessions: AdminSessionManager;
  publicDir?: string;
};
```

Add request auth helpers:

```ts
const getAccess = async (req: Request, store: BoardDataStore, sessions: AdminSessionManager): Promise<AccessContext | null> => {
  const session = sessionFromRequest(req, sessions);
  if (session) {
    return adminAccess(session.username);
  }
  const token = parseBearerToken(req.headers.authorization);
  if (!token) {
    return null;
  }
  const authenticated = await store.authenticateApiToken(token);
  return authenticated ? tokenAccess(authenticated.tokenId, authenticated.projectIds) : null;
};
```

Add guards:

```ts
const requireAccess = (store: BoardDataStore, sessions: AdminSessionManager): RequestHandler =>
  asyncHandler(async (req, res, next) => {
    const access = await getAccess(req, store, sessions);
    if (!access) {
      res.status(401).json({ error: "Authentication required" });
      return;
    }
    res.locals.access = access;
    next();
  });

const requireAdmin = (store: BoardDataStore, sessions: AdminSessionManager): RequestHandler =>
  asyncHandler(async (req, res, next) => {
    const access = await getAccess(req, store, sessions);
    if (!access) {
      res.status(401).json({ error: "Authentication required" });
      return;
    }
    if (access.kind !== "admin") {
      res.status(403).json({ error: "Admin access required" });
      return;
    }
    res.locals.access = access;
    next();
  });
```

Convert every existing route handler that calls the store to `await`. Pass `res.locals.access` into project-scoped methods.

Admin-only routes:

- `POST /api/projects`
- `PATCH /api/projects/:id`
- `GET /api/tokens`
- `POST /api/tokens`
- `PATCH /api/tokens/:id`
- `POST /api/tokens/:id/revoke`

Public routes:

- `GET /api/health`
- `GET /api/mcp-info`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`

Auth routes:

```ts
app.post("/api/auth/login", (req, res) => {
  const parsed = z.object({ username: z.string(), password: z.string() }).parse(req.body);
  const session = sessions.login(parsed.username, parsed.password);
  if (!session) {
    res.status(401).json({ error: "Invalid username or password" });
    return;
  }
  setSessionCookie(res, session.cookie);
  res.json({ authenticated: true, username: session.username });
});
```

- [ ] **Step 4: Update existing API tests to authenticate as admin**

For each existing API test that uses board endpoints, create an agent and log in:

```ts
const { app } = createAuthedApp();
const agent = request.agent(app);
await agent.post("/api/auth/login").send({ username: "admin", password: "secret" }).expect(200);
```

Replace `request(app)` with `agent` for admin-owned calls.

- [ ] **Step 5: Run API tests and verify GREEN**

Run:

```bash
npm test -- tests/api.test.ts
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/server/app.ts tests/api.test.ts
git commit -m "feat: protect rest api with admin and token auth"
```

---

### Task 9: Enforce MCP Bearer Token Auth and Scoping

**Files:**

- Create: `src/server/mcpTools.ts`
- Modify: `src/server/mcp.ts`
- Create: `tests/mcp-auth.test.ts`

- [ ] **Step 1: Write MCP auth and scoped handler tests**

Create `tests/mcp-auth.test.ts`:

```ts
import type { Pool } from "pg";
import request from "supertest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createAdminSessionManager } from "../src/server/auth.js";
import { createApp } from "../src/server/app.js";
import { createMcpToolHandlers } from "../src/server/mcpTools.js";
import { adminAccess, tokenAccess } from "../src/server/store.js";
import type { PostgresBoardStore } from "../src/server/storage.js";
import { closeTestPool, createTestStore } from "./helpers/store.js";

let pool: Pool;
let store: PostgresBoardStore;

beforeEach(async () => {
  const created = await createTestStore();
  pool = created.pool;
  store = created.store;
});

afterEach(async () => {
  await closeTestPool(pool);
});

describe("MCP auth", () => {
  it("requires a bearer token for the MCP route", async () => {
    const sessions = createAdminSessionManager({ username: "admin", password: "secret" });
    const app = createApp({ store, sessions });

    await request(app).post("/mcp").send({ jsonrpc: "2.0", id: 1, method: "tools/list" }).expect(401);
    await request(app)
      .post("/mcp")
      .set("Authorization", "Bearer invalid")
      .send({ jsonrpc: "2.0", id: 1, method: "tools/list" })
      .expect(401);
  });

  it("scopes MCP tool handlers to mapped projects", async () => {
    const appProject = await store.createProject({ key: "APP", title: "App" });
    const apiProject = await store.createProject({ key: "API", title: "API" });
    await store.createIdea(adminAccess("admin"), { projectId: appProject.id, title: "Visible" });
    await store.createIdea(adminAccess("admin"), { projectId: apiProject.id, title: "Hidden" });

    const handlers = createMcpToolHandlers(store, tokenAccess("token-1", [appProject.id]));

    await expect(handlers.create_project({ key: "NEW", title: "Nope" })).rejects.toThrow(
      "Admin access required"
    );
    await expect(
      handlers.create_idea({ projectId: apiProject.id, title: "Forbidden" })
    ).rejects.toThrow("Token is not allowed to access project");

    const listed = await handlers.list_ideas({});
    expect(listed).toEqual({ items: [expect.objectContaining({ title: "Visible" })] });
  });
});
```

- [ ] **Step 2: Run MCP tests and verify RED**

Run:

```bash
npm test -- tests/mcp-auth.test.ts
```

Expected: FAIL because `mcpTools.ts` does not exist and `/mcp` accepts unauthenticated requests.

- [ ] **Step 3: Extract MCP tool handlers**

Create `src/server/mcpTools.ts`:

```ts
import { boardColumns, documentKinds } from "../shared/types.js";
import type { BoardDataStore, AccessContext } from "./store.js";
import { isAdminAccess } from "./store.js";

export const createMcpToolHandlers = (store: BoardDataStore, access: AccessContext) => ({
  async list_projects() {
    return { items: await store.listProjects(access) };
  },
  async create_project(input: { key: string; title: string; summary?: string }) {
    if (!isAdminAccess(access)) {
      throw new Error("Admin access required");
    }
    return { item: await store.createProject(input) };
  },
  async list_ideas(input: { projectId?: string }) {
    return { items: await store.listIdeas(access, input.projectId) };
  },
  async create_idea(input: Parameters<BoardDataStore["createIdea"]>[1]) {
    return { item: await store.createIdea(access, input) };
  },
  async mark_idea_ready(input: { id: string }) {
    return { item: await store.markIdeaReady(access, input.id) };
  },
  async list_board(input: { projectId?: string }) {
    return { columns: await store.getBoardColumns(access, input.projectId) };
  },
  async move_board_card(input: { id: string; column: (typeof boardColumns)[number] }) {
    return { item: await store.moveBoardCard(access, input.id, input.column) };
  },
  async create_document(input: Parameters<BoardDataStore["createDocument"]>[1]) {
    return { item: await store.createDocument(access, input) };
  },
  async list_documents(input: { projectId?: string }) {
    return { items: await store.listDocuments(access, input.projectId) };
  },
  async read_state() {
    return await store.getState(access);
  }
});
```

- [ ] **Step 4: Require bearer tokens in `createMcpRouter`**

Modify `src/server/mcp.ts`:

- Accept `BoardDataStore` instead of `BoardStore`.
- In `router.post("/")`, parse `Authorization` with `parseBearerToken`.
- Call `store.authenticateApiToken`.
- Return `401` for missing, invalid, or revoked tokens.
- Create `tokenAccess(authenticated.tokenId, authenticated.projectIds)`.
- Pass access into `createMcpServer(store, access)`.
- Register tools by delegating to `createMcpToolHandlers`.
- Register `vibepod-board://state` by returning `handlers.read_state()`.

- [ ] **Step 5: Run MCP tests and API smoke tests**

Run:

```bash
npm test -- tests/mcp-auth.test.ts tests/api.test.ts
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/server/mcp.ts src/server/mcpTools.ts tests/mcp-auth.test.ts
git commit -m "feat: scope mcp access by project token"
```

---

### Task 10: Start the Server with PostgreSQL and Admin Env Validation

**Files:**

- Modify: `src/server/index.ts`
- Modify: `tests/api.test.ts`

- [ ] **Step 1: Add a startup configuration test**

Append to `tests/api.test.ts`:

```ts
it("reports auth state through /api/auth/me", async () => {
  const { app } = createAuthedApp();
  const agent = request.agent(app);

  expect((await agent.get("/api/auth/me").expect(200)).body).toEqual({ authenticated: false });

  await agent.post("/api/auth/login").send({ username: "admin", password: "secret" }).expect(200);

  expect((await agent.get("/api/auth/me").expect(200)).body).toEqual({
    authenticated: true,
    username: "admin"
  });
});
```

- [ ] **Step 2: Run the API auth-state test and verify current state**

Run:

```bash
npm test -- tests/api.test.ts
```

Expected: PASS after Task 8, confirming auth endpoints work before startup wiring changes.

- [ ] **Step 3: Update `src/server/index.ts`**

Replace JSON file startup with async PostgreSQL startup:

```ts
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createAdminSessionManager } from "./auth.js";
import { createApp } from "./app.js";
import { createPool, initializeDatabase } from "./db.js";
import { PostgresBoardStore } from "./storage.js";

const currentDir = dirname(fileURLToPath(import.meta.url));
const publicDir = resolve(currentDir, "../client");
const port = Number(process.env.PORT ?? 3000);
const host = process.env.HOST ?? "0.0.0.0";

const databaseUrl = process.env.DATABASE_URL;
const adminUsername = process.env.ADMIN_USERNAME;
const adminPassword = process.env.ADMIN_PASSWORD;

if (!databaseUrl) {
  throw new Error("DATABASE_URL is required");
}
if (process.env.NODE_ENV === "production" && (!adminUsername || !adminPassword)) {
  throw new Error("ADMIN_USERNAME and ADMIN_PASSWORD are required in production");
}

const pool = createPool(databaseUrl);
await initializeDatabase(pool);

const store = new PostgresBoardStore(pool);
const sessions = createAdminSessionManager({
  username: adminUsername ?? "admin",
  password: adminPassword ?? "admin"
});

const app = createApp({
  store,
  sessions,
  publicDir: existsSync(resolve(publicDir, "index.html")) ? publicDir : undefined
});

app.listen(port, host, () => {
  console.log(`vibepod-board listening on http://${host}:${port}`);
  console.log("Storage: PostgreSQL");
});
```

- [ ] **Step 4: Run typecheck**

Run:

```bash
npm run typecheck
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/index.ts tests/api.test.ts
git commit -m "feat: start server with postgres storage"
```

---

### Task 11: Add Login UI, Token Management UI, and Token-Aware MCP Examples

**Files:**

- Modify: `src/client/main.tsx`
- Modify: `src/client/styles.css`
- Modify: `src/shared/integrationExamples.ts`
- Create: `tests/ui-auth-token.test.tsx`
- Create: `tests/token-config-utils.test.ts`
- Modify: `tests/integration-examples.test.ts`

- [ ] **Step 1: Write token config utility tests**

Create `tests/token-config-utils.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { integrationExamplesForToken } from "../src/shared/integrationExamples.js";

describe("token-aware MCP config examples", () => {
  it("includes the bearer token in every config example", () => {
    const examples = integrationExamplesForToken("vbp_test_token");

    for (const example of examples) {
      expect(`${example.command ?? ""}\n${example.config}`).toContain("vbp_test_token");
      expect(`${example.command ?? ""}\n${example.config}`).toContain("Authorization");
    }
  });
});
```

- [ ] **Step 2: Run token config tests and verify RED**

Run:

```bash
npm test -- tests/token-config-utils.test.ts
```

Expected: FAIL because `integrationExamplesForToken` does not exist.

- [ ] **Step 3: Add token-aware integration examples**

Modify `src/shared/integrationExamples.ts`:

```ts
export const bearerTokenExample = "<vibepod-board-token>";

export const authorizationHeader = (token = bearerTokenExample) => `Bearer ${token}`;

export const integrationExamplesForToken = (token = bearerTokenExample): IntegrationExample[] => [
  {
    id: "claude-code",
    name: "Claude Code",
    description: "Add vibepod-board as a remote HTTP MCP server with a bearer token.",
    docsUrl: "https://docs.anthropic.com/en/docs/claude-code/mcp",
    command:
      `claude mcp add-json vibepod-board '{"type":"http","url":"http://vibepod-board:3000/mcp","headers":{"Authorization":"${authorizationHeader(token)}"}}'`,
    configLabel: "Project .mcp.json alternative",
    config: `{
  "mcpServers": {
    "vibepod-board": {
      "type": "http",
      "url": "http://vibepod-board:3000/mcp",
      "headers": {
        "Authorization": "${authorizationHeader(token)}"
      }
    }
  }
}`,
    verify: "Run claude mcp list, then open Claude Code and use /mcp."
  },
  {
    id: "codex",
    name: "Codex",
    description: "Use the Codex MCP config with an Authorization header.",
    docsUrl: "https://developers.openai.com/codex/mcp",
    configLabel: "~/.codex/config.toml",
    config: `[mcp_servers.vibepod-board]
url = "http://vibepod-board:3000/mcp"
http_headers = { Authorization = "${authorizationHeader(token)}" }`,
    verify: "Run codex mcp list, then use /mcp in the Codex TUI."
  },
  {
    id: "auggie",
    name: "Auggie",
    description: "Persist the board endpoint with a bearer token in Augment settings.",
    docsUrl: "https://docs.augmentcode.com/cli/integrations",
    configLabel: "~/.augment/settings.json",
    config: `{
  "mcpServers": {
    "vibepod-board": {
      "type": "http",
      "url": "http://vibepod-board:3000/mcp",
      "headers": {
        "Authorization": "${authorizationHeader(token)}"
      }
    }
  }
}`,
    verify: "Run auggie mcp list or open Auggie and use /mcp."
  },
  {
    id: "opencode",
    name: "OpenCode",
    description: "Add vibepod-board as a remote MCP server with request headers.",
    docsUrl: "https://opencode.ai/docs/mcp-servers/",
    configLabel: "opencode.json",
    config: `{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "vibepod-board": {
      "type": "remote",
      "url": "http://vibepod-board:3000/mcp",
      "enabled": true,
      "headers": {
        "Authorization": "${authorizationHeader(token)}"
      }
    }
  }
}`,
    verify: "Run opencode mcp list, then prompt OpenCode to use vibepod-board."
  }
];

export const integrationExamples = integrationExamplesForToken();
```

- [ ] **Step 4: Run config tests**

Run:

```bash
npm test -- tests/token-config-utils.test.ts tests/integration-examples.test.ts
```

Expected: PASS.

- [ ] **Step 5: Write focused React UI tests**

Create `tests/ui-auth-token.test.tsx`:

```tsx
// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "../src/client/main.js";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("admin auth UI", () => {
  it("shows login when the admin is unauthenticated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path === "/api/auth/me") {
          return new Response(JSON.stringify({ authenticated: false }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
          });
        }
        return new Response("{}", { status: 404 });
      })
    );

    render(<AppShell />);

    expect(await screen.findByLabelText("Username")).toBeTruthy();
    expect(await screen.findByLabelText("Password")).toBeTruthy();
  });

  it("opens token management for authenticated admins", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path === "/api/auth/me") {
          return new Response(JSON.stringify({ authenticated: true, username: "admin" }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
          });
        }
        if (path === "/api/projects") {
          return new Response(JSON.stringify({ items: [{ id: "project-1", key: "APP", title: "App", summary: "", createdAt: "", updatedAt: "" }] }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
          });
        }
        if (path === "/api/ideas") {
          return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
        }
        if (path === "/api/board") {
          return new Response(JSON.stringify({ columns: { ready: [], planned: [], in_progress: [], review: [], done: [] } }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
          });
        }
        if (path === "/api/documents") {
          return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
        }
        if (path === "/api/tokens") {
          return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
        }
        return new Response("{}", { status: 404 });
      })
    );

    render(<AppShell />);
    await userEvent.click(await screen.findByRole("button", { name: "API Tokens" }));

    expect(await screen.findByText("Create Token")).toBeTruthy();
  });
});
```

- [ ] **Step 6: Run UI tests and verify RED**

Run:

```bash
npm test -- tests/ui-auth-token.test.tsx
```

Expected: FAIL because `AppShell` is not exported and the UI has no login/token controls.

- [ ] **Step 7: Implement auth state and login UI**

Modify `src/client/main.tsx`:

- Export `AppShell`.
- Add `credentials: "include"` to the shared `api()` helper.
- On boot, call `/api/auth/me` before `loadState`.
- If unauthenticated, render a login form with `Username` and `Password` fields.
- On login success, call `loadState`.
- On `401` from API calls, return to the login screen.
- Add a `Logout` button in the topbar.

- [ ] **Step 8: Implement token-management modal**

In `src/client/main.tsx`, add state:

```ts
const [isTokenManagerOpen, setIsTokenManagerOpen] = useState(false);
const [tokens, setTokens] = useState<ApiTokenSummary[]>([]);
const [createdToken, setCreatedToken] = useState<string>("");
const [tokenDraft, setTokenDraft] = useState<CreateApiTokenInput>({ name: "", projectIds: [] });
```

Add an `API Tokens` button in the topbar using a lucide `KeyRound` icon.

The modal must:

- Load `GET /api/tokens` when opened.
- Render existing tokens with project badges, created time, last-used time, and revoked state.
- Create tokens with `POST /api/tokens`.
- Show `createdToken` once after creation.
- Render `integrationExamplesForToken(createdToken)` when a new token exists.
- Revoke tokens with `POST /api/tokens/:id/revoke`.

- [ ] **Step 9: Add styles**

Modify `src/client/styles.css` with classes:

- `.login-shell`
- `.login-panel`
- `.token-list`
- `.token-created`
- `.token-config`

Use the existing palette and card radius. Do not introduce a new one-note color theme.

- [ ] **Step 10: Run UI and type checks**

Run:

```bash
npm test -- tests/ui-auth-token.test.tsx tests/token-config-utils.test.ts tests/integration-examples.test.ts
npm run typecheck
```

Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/client/main.tsx src/client/styles.css src/shared/integrationExamples.ts tests/ui-auth-token.test.tsx tests/token-config-utils.test.ts tests/integration-examples.test.ts
git commit -m "feat: add admin login and token management ui"
```

---

### Task 12: Update Docker, Environment, and Docs

**Files:**

- Modify: `compose.yml`
- Modify: `.env.example`
- Modify: `Dockerfile`
- Modify: `README.md`
- Modify: `docs/integration-guide.md`
- Modify: `tests/compose.test.ts`

- [ ] **Step 1: Write compose expectations**

Modify `tests/compose.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const compose = readFileSync(join(process.cwd(), "compose.yml"), "utf8");

describe("compose networking and database", () => {
  it("attaches the board service to the shared VibePod network", () => {
    expect(compose).toContain("vibepod:");
    expect(compose).toContain("aliases:");
    expect(compose).toContain("- vibepod-board");
    expect(compose).toContain("name: ${VIBEPOD_NETWORK:-vibepod-network}");
    expect(compose).toContain("external: true");
  });

  it("runs PostgreSQL as the board storage service", () => {
    expect(compose).toContain("postgres:");
    expect(compose).toContain("image: postgres:16-alpine");
    expect(compose).toContain("DATABASE_URL:");
    expect(compose).toContain("vibepod-board-postgres-data:");
    expect(compose).not.toContain("DATA_DIR: /data");
    expect(compose).not.toContain("vibepod-board-data:/data");
  });
});
```

- [ ] **Step 2: Run compose test and verify RED**

Run:

```bash
npm test -- tests/compose.test.ts
```

Expected: FAIL because Compose still uses `/data` and has no PostgreSQL service.

- [ ] **Step 3: Update Compose**

Modify `compose.yml`:

```yaml
services:
  board:
    build: .
    image: vibepod-board:dev
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "3000:3000"
    environment:
      PORT: "3000"
      HOST: "0.0.0.0"
      DATABASE_URL: postgres://${POSTGRES_USER:-vibepod}:${POSTGRES_PASSWORD:-vibepod}@postgres:5432/${POSTGRES_DB:-vibepod_board}
      ADMIN_USERNAME: ${ADMIN_USERNAME:-admin}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD:-admin-password}
      GITHUB_TOKEN: ${GITHUB_TOKEN:-}
      GITHUB_REPOSITORY: ${GITHUB_REPOSITORY:-}
    networks:
      vibepod:
        aliases:
          - vibepod-board

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-vibepod_board}
      POSTGRES_USER: ${POSTGRES_USER:-vibepod}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-vibepod}
    volumes:
      - vibepod-board-postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-vibepod} -d ${POSTGRES_DB:-vibepod_board}"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks:
      - vibepod

volumes:
  vibepod-board-postgres-data:

networks:
  vibepod:
    name: ${VIBEPOD_NETWORK:-vibepod-network}
    external: true
```

- [ ] **Step 4: Update env example**

Modify `.env.example`:

```dotenv
PORT=3000
HOST=0.0.0.0
VIBEPOD_NETWORK=vibepod-network

POSTGRES_DB=vibepod_board
POSTGRES_USER=vibepod
POSTGRES_PASSWORD=vibepod
DATABASE_URL=postgres://vibepod:vibepod@postgres:5432/vibepod_board

ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me

# Optional. When set, ready ideas create real GitHub issues before entering the board.
# GITHUB_TOKEN=ghp_xxx
# GITHUB_REPOSITORY=owner/repo
```

- [ ] **Step 5: Update Dockerfile**

Remove:

```dockerfile
ENV DATA_DIR=/data
RUN mkdir -p /data && chown -R node:node /app /data
VOLUME ["/data"]
```

Use:

```dockerfile
RUN chown -R node:node /app
```

- [ ] **Step 6: Update docs**

Update `README.md`:

- Runtime state is stored in PostgreSQL.
- `docker compose up --build` starts both board and PostgreSQL.
- Admin login uses `ADMIN_USERNAME` and `ADMIN_PASSWORD`.
- Tokens are created from the browser UI under `API Tokens`.
- JSON import command is `npm run import:json -- ./data/board.json`.
- Verification includes a database-backed `npm test`.

Update `docs/integration-guide.md`:

- MCP endpoint requires `Authorization: Bearer <token>`.
- Tokens are scoped to one or more projects.
- Token values are shown once during creation.
- Existing client examples include the bearer header.

- [ ] **Step 7: Run docs/config tests**

Run:

```bash
npm test -- tests/compose.test.ts tests/integration-examples.test.ts
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add compose.yml .env.example Dockerfile README.md docs/integration-guide.md tests/compose.test.ts
git commit -m "chore: run board with postgres compose service"
```

---

### Task 13: Final Verification and Cleanup

**Files:**

- Check: all modified files

- [ ] **Step 1: Run the full test suite**

Run:

```bash
npm test
```

Expected: PASS with all tests green.

- [ ] **Step 2: Run TypeScript typecheck**

Run:

```bash
npm run typecheck
```

Expected: PASS.

- [ ] **Step 3: Run production build**

Run:

```bash
npm run build
```

Expected: PASS and `dist/` contains built server/client output.

- [ ] **Step 4: Build Docker image**

Run:

```bash
docker build -t vibepod-board:dev .
```

Expected: PASS.

- [ ] **Step 5: Run Compose smoke test**

Ensure the shared network exists:

```bash
docker network create vibepod-network || true
```

Start services:

```bash
ADMIN_USERNAME=admin ADMIN_PASSWORD=admin-password docker compose up --build -d
```

Smoke test:

```bash
curl -fsS http://localhost:3000/api/health
```

Expected response:

```json
{"ok":true,"service":"vibepod-board","mcp":"/mcp"}
```

Stop services:

```bash
docker compose down
```

- [ ] **Step 6: Inspect git diff**

Run:

```bash
git status --short
git diff --stat
```

Expected: only intentional changes from this plan are present.

- [ ] **Step 7: Commit final cleanup if files changed after the previous task**

```bash
git add .
git commit -m "chore: finalize postgres auth migration"
```

Skip this commit when `git status --short` is empty.
