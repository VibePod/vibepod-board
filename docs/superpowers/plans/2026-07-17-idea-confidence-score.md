# Idea Confidence Score Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author confidence scores on Ideas as the single source of truth, mirror them onto linked board cards, and surface the score in the idea list and idea detail modal.

**Architecture:** The idea holds the canonical `readinessScore`/`readinessReason`/`readinessEvaluatedAt`. `setIdeaReadiness` writes the idea's readiness columns and mirrors the same values onto the linked board card — neither write bumps `updated_at` (preserving the staleness signal). `setCardReadiness` redirects to `setIdeaReadiness` when the card has a linked idea. `ensureBoardCard` carries idea readiness onto a card at promotion time. The client shows an idea readiness badge + a modal Confidence section, and computes a card's staleness from its linked idea.

**Tech Stack:** TypeScript, Node, Express, PostgreSQL (`pg`), a JSON-backed in-memory `BoardStore` (unit tests), React + Mantine (client), Vitest.

Two stores implement the same behavior:
- `PostgresBoardStore` (`src/server/storage.ts:187`) — `implements BoardDataStore`, async, access-aware. Used by the app/API/MCP.
- `BoardStore` (`src/server/storage.ts:1266`) — sync, JSON-backed, no access arg. Used directly by unit tests (`tests/board-store-readiness.test.ts`).

Reference (existing card readiness): `setCardReadiness` Postgres `storage.ts:606-642`, in-memory `storage.ts:1528-1537`; `normalizeReadinessInput` `storage.ts:85-94`; MCP tool `mcp.ts:176-189`; API route `app.ts:286-294`.

---

### Task 1: Add readiness fields to the `Idea` type

**Files:**
- Modify: `src/shared/types.ts:18-34`

- [ ] **Step 1: Add the fields**

In the `Idea` type, after `repositoryRemoteUrl?: string;` (line 31) add:

```ts
  readinessScore?: number;
  readinessReason?: string;
  readinessEvaluatedAt?: string;
```

(These mirror `BoardCard` lines 49-51. Reuse the existing `SetCardReadinessInput` — do NOT add a new input type.)

- [ ] **Step 2: Typecheck**

Run: `npm run typecheck`
Expected: PASS (no code depends on the new optional fields yet).

- [ ] **Step 3: Commit**

```bash
git add src/shared/types.ts
git commit -m "feat: add readiness fields to Idea type"
```

---

### Task 2: Add idea readiness columns to the schema

**Files:**
- Modify: `src/server/db.ts:99-101`
- Test: `tests/postgres-schema.test.ts`

- [ ] **Step 1: Write the failing test**

In `tests/postgres-schema.test.ts`, after the `"stores readiness fields on board cards"` test (ends line 102), add:

```ts
  it("stores readiness fields on ideas", async () => {
    await initializeDatabase(pool);

    const columns = await pool.query<{ column_name: string }>(
      `select column_name
       from information_schema.columns
       where table_schema = 'public' and table_name = 'ideas'
       order by ordinal_position`
    );

    const names = columns.rows.map((row) => row.column_name);
    expect(names).toContain("readiness_score");
    expect(names).toContain("readiness_reason");
    expect(names).toContain("readiness_evaluated_at");
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/postgres-schema.test.ts -t "stores readiness fields on ideas"`
Expected: FAIL — the ideas table has no `readiness_*` columns.

(Requires a test Postgres — see `tests/helpers/postgres.ts`. If the DB is unavailable, note it and proceed; the schema change is verified by the assertion once a DB is present.)

- [ ] **Step 3: Add the migration**

In `src/server/db.ts`, after line 101 (`alter table board_cards add column if not exists readiness_evaluated_at timestamptz;`) add:

```sql
alter table ideas add column if not exists readiness_score integer;
alter table ideas add column if not exists readiness_reason text;
alter table ideas add column if not exists readiness_evaluated_at timestamptz;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/postgres-schema.test.ts -t "stores readiness fields on ideas"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/db.ts tests/postgres-schema.test.ts
git commit -m "feat: add readiness columns to ideas table"
```

---

### Task 3: Map idea readiness in the Postgres row mapper

**Files:**
- Modify: `src/server/storage.ts:122-138` (`IdeaRow`)
- Modify: `src/server/storage.ts:1209-1225` (`ideaFromRow`)

- [ ] **Step 1: Extend `IdeaRow`**

In the `IdeaRow` type, after `repository_remote_url: string | null;` (line 135) add:

```ts
  readiness_score: number | null;
  readiness_reason: string | null;
  readiness_evaluated_at: Date | string | null;
```

- [ ] **Step 2: Extend `ideaFromRow`**

In `ideaFromRow`, after `repositoryRemoteUrl: row.repository_remote_url ?? undefined,` (line 1222) add:

```ts
  readinessScore: row.readiness_score ?? undefined,
  readinessReason: row.readiness_reason ?? undefined,
  readinessEvaluatedAt: row.readiness_evaluated_at ? rowTimestamp(row.readiness_evaluated_at) : undefined,
```

- [ ] **Step 3: Typecheck**

Run: `npm run typecheck`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/server/storage.ts
git commit -m "feat: map idea readiness fields from db row"
```

---

### Task 4: `setIdeaReadiness` — interface + both stores

**Files:**
- Modify: `src/server/store.ts:65` (interface)
- Modify: `src/server/storage.ts` (Postgres — add after `setCardReadiness`, ~line 642; in-memory — add after `setCardReadiness`, ~line 1537)
- Test: `tests/board-store-readiness.test.ts`

- [ ] **Step 1: Write the failing test (in-memory store)**

In `tests/board-store-readiness.test.ts`, add inside `describe("BoardStore readiness", ...)`:

```ts
  it("sets idea readiness and mirrors it onto the linked card without bumping updatedAt", () => {
    const project = store.createProject({ key: "IDR", title: "Idea readiness" });
    const idea = store.createIdea({ projectId: project.id, title: "Scored idea" });
    store.markIdeaReady(idea.id);
    const cardBefore = store.getBoardColumns(project.id).ready[0];
    const ideaBefore = store.listIdeas(project.id).find((i) => i.id === idea.id)!;

    const scored = store.setIdeaReadiness(idea.id, { score: 7, reason: "Clear scope" });
    expect(scored.readinessScore).toBe(7);
    expect(scored.readinessReason).toBe("Clear scope");
    expect(scored.readinessEvaluatedAt).toBeDefined();
    expect(scored.updatedAt).toBe(ideaBefore.updatedAt);

    const card = store.getBoardColumns(project.id).ready[0];
    expect(card.readinessScore).toBe(7);
    expect(card.readinessReason).toBe("Clear scope");
    expect(card.updatedAt).toBe(cardBefore.updatedAt);
  });

  it("validates idea readiness score and reason", () => {
    const project = store.createProject({ key: "IDV", title: "Idea validate" });
    const idea = store.createIdea({ projectId: project.id, title: "Idea" });
    expect(() => store.setIdeaReadiness(idea.id, { score: 0, reason: "r" })).toThrow(
      "Readiness score must be an integer from 1 to 10"
    );
    expect(() => store.setIdeaReadiness(idea.id, { score: 5, reason: " " })).toThrow(
      "Readiness reason is required"
    );
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/board-store-readiness.test.ts -t "sets idea readiness"`
Expected: FAIL — `store.setIdeaReadiness is not a function`.

- [ ] **Step 3: Add the interface method**

In `src/server/store.ts`, after line 65 (`setCardReadiness(...): Promise<BoardCard>;`) add:

```ts
  setIdeaReadiness(access: AccessContext, id: string, input: SetCardReadinessInput): Promise<Idea>;
```

- [ ] **Step 4: Implement in the in-memory `BoardStore`**

In `src/server/storage.ts`, immediately after the in-memory `setCardReadiness` method (ends ~line 1537) add:

```ts
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
    this.addActivity("idea.readiness", `Set readiness ${score}/10: ${idea.title}`);
    this.save();
    return clone(idea);
  }
```

(Note: neither the idea nor the card has `updatedAt` reassigned — that is the staleness contract.)

- [ ] **Step 5: Implement in `PostgresBoardStore`**

In `src/server/storage.ts`, immediately after the Postgres `setCardReadiness` method (ends line 642) add:

```ts
  async setIdeaReadiness(
    access: AccessContext,
    id: string,
    input: SetCardReadinessInput
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
        [id, score, reason, timestamp]
      );
      await client.query(
        `update board_cards
         set readiness_score = $2,
             readiness_reason = $3,
             readiness_evaluated_at = $4
         where idea_id = $1`,
        [id, score, reason, timestamp]
      );
      const idea = ideaFromRow(result.rows[0]);
      await this.addActivity(
        client,
        "idea.readiness",
        `Set readiness ${score}/10: ${idea.title}`,
        timestamp
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
```

(Uses the existing private `requireIdea(client, id)` — see its use at `storage.ts:462`.)

- [ ] **Step 6: Run the tests**

Run: `npx vitest run tests/board-store-readiness.test.ts`
Expected: PASS (new + existing). Then `npm run typecheck` — PASS.

- [ ] **Step 7: Commit**

```bash
git add src/server/store.ts src/server/storage.ts tests/board-store-readiness.test.ts
git commit -m "feat: add setIdeaReadiness to stores, mirror to linked card"
```

---

### Task 5: Redirect `setCardReadiness` to the idea

**Files:**
- Modify: `src/server/storage.ts:606-642` (Postgres `setCardReadiness`)
- Modify: `src/server/storage.ts:1528-1537` (in-memory `setCardReadiness`)
- Test: `tests/board-store-readiness.test.ts`

- [ ] **Step 1: Write the failing test**

In `tests/board-store-readiness.test.ts` add:

```ts
  it("redirects card scoring to the linked idea", () => {
    const project = store.createProject({ key: "RDR", title: "Redirect" });
    const idea = store.createIdea({ projectId: project.id, title: "Linked" });
    store.markIdeaReady(idea.id);
    const card = store.getBoardColumns(project.id).ready[0];

    const scoredCard = store.setCardReadiness(card.id, { score: 4, reason: "Some risk" });
    expect(scoredCard.readinessScore).toBe(4);

    const updatedIdea = store.listIdeas(project.id).find((i) => i.id === idea.id)!;
    expect(updatedIdea.readinessScore).toBe(4);
    expect(updatedIdea.readinessReason).toBe("Some risk");
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/board-store-readiness.test.ts -t "redirects card scoring"`
Expected: FAIL — the idea's `readinessScore` is `undefined` (card write did not touch the idea).

- [ ] **Step 3: Update in-memory `setCardReadiness`**

Replace the in-memory `setCardReadiness` body (`storage.ts:1528-1537`) with:

```ts
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
    this.addActivity("board.readiness", `Set readiness ${score}/10: ${card.title}`);
    this.save();
    return clone(card);
  }
```

- [ ] **Step 4: Update Postgres `setCardReadiness`**

Replace the Postgres `setCardReadiness` body (`storage.ts:606-642`) with:

```ts
  async setCardReadiness(
    access: AccessContext,
    id: string,
    input: SetCardReadinessInput
  ): Promise<BoardCard> {
    const existing = await this.pool.query<BoardCardRow>(
      "select * from board_cards where id = $1",
      [id]
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
        [id]
      );
      return boardCardFromRow(reread.rows[0]);
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
      [id, score, reason, timestamp]
    );
    return boardCardFromRow(result.rows[0]);
  }
```

(The linked-idea path delegates to `setIdeaReadiness`, which also records the activity event. The no-idea fallback keeps the old card-local write; the `board.readiness` activity for that rare path is dropped, which is acceptable since all cards today have an idea.)

- [ ] **Step 5: Run the tests + typecheck**

Run: `npx vitest run tests/board-store-readiness.test.ts`
Expected: PASS. Then `npm run typecheck` — PASS.

- [ ] **Step 6: Commit**

```bash
git add src/server/storage.ts tests/board-store-readiness.test.ts
git commit -m "feat: redirect card scoring to the linked idea"
```

---

### Task 6: Carry idea readiness onto a promoted card

**Files:**
- Modify: `src/server/storage.ts:1447-1459` (in-memory `ensureBoardCard` insert literal)
- Modify: `src/server/storage.ts:1063-1083` (Postgres `ensureBoardCard` insert)
- Test: `tests/board-store-readiness.test.ts`

- [ ] **Step 1: Write the failing test**

In `tests/board-store-readiness.test.ts` add:

```ts
  it("carries idea readiness onto a card created at promotion time", () => {
    const project = store.createProject({ key: "CRY", title: "Carry" });
    const idea = store.createIdea({ projectId: project.id, title: "Scored then promoted" });
    store.setIdeaReadiness(idea.id, { score: 8, reason: "Ready to ship" });

    store.markIdeaReady(idea.id);
    const card = store.getBoardColumns(project.id).ready[0];
    expect(card.readinessScore).toBe(8);
    expect(card.readinessReason).toBe("Ready to ship");
    expect(card.readinessEvaluatedAt).toBeDefined();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/board-store-readiness.test.ts -t "carries idea readiness"`
Expected: FAIL — the newly created card has `readinessScore === undefined`.

- [ ] **Step 3: Update in-memory card literal**

In `ensureBoardCard` (in-memory), in the `const card: BoardCard = {...}` literal (`storage.ts:1447-1459`), after `labels: [...idea.labels],` (line 1456) add:

```ts
      readinessScore: idea.readinessScore,
      readinessReason: idea.readinessReason,
      readinessEvaluatedAt: idea.readinessEvaluatedAt,
```

- [ ] **Step 4: Update Postgres insert**

In `ensureBoardCard` (Postgres), update the INSERT (`storage.ts:1063-1083`). Change the column list and values to include readiness:

```ts
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
        idea.readinessEvaluatedAt ?? null
      ]
    );
```

- [ ] **Step 5: Run the tests + typecheck**

Run: `npx vitest run tests/board-store-readiness.test.ts`
Expected: PASS. Then `npm run typecheck` — PASS.

- [ ] **Step 6: Commit**

```bash
git add src/server/storage.ts tests/board-store-readiness.test.ts
git commit -m "feat: carry idea readiness onto promoted board card"
```

---

### Task 7: HTTP route `POST /api/ideas/:id/readiness`

**Files:**
- Modify: `src/server/app.ts:286-294` (add a route after the board readiness route)
- Test: `tests/api.test.ts`

- [ ] **Step 1: Write the failing test**

In `tests/api.test.ts`, add a test that logs in as admin, creates a project + idea, scores it, and reads it back. Mirror the existing agent/login pattern (`login` helper at `api.test.ts:29`). Add inside the top-level `describe`:

```ts
  it("sets idea readiness over HTTP", async () => {
    const app = createApp({ store, sessions });
    const agent = await login(app);

    const project = (
      await agent.post("/api/projects").send({ key: "IRA", title: "Idea readiness api" }).expect(201)
    ).body.item;
    const idea = (
      await agent
        .post("/api/ideas")
        .send({ projectId: project.id, title: "Scored via api" })
        .expect(201)
    ).body.item;

    const scored = (
      await agent
        .post(`/api/ideas/${idea.id}/readiness`)
        .send({ score: 6, reason: "Reasonable" })
        .expect(200)
    ).body.item;

    expect(scored.readinessScore).toBe(6);
    expect(scored.readinessReason).toBe("Reasonable");

    await agent.post(`/api/ideas/${idea.id}/readiness`).send({ score: 99, reason: "x" }).expect(400);
  });
```

(Confirm the exact status codes for project/idea creation against neighboring tests in `api.test.ts` and adjust `.expect(201)` if the repo uses `200`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/api.test.ts -t "sets idea readiness over HTTP"`
Expected: FAIL — route returns 404.

- [ ] **Step 3: Add the route**

In `src/server/app.ts`, immediately after the `POST /api/board/:id/readiness` route (ends line 294) add:

```ts
  app.post(
    "/api/ideas/:id/readiness",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const input = readinessSchema.parse(req.body);
      const item = await store.setIdeaReadiness(accessFromResponse(req), routeParam(req.params.id), input);
      res.json({ item });
    })
  );
```

(Reuses the existing `readinessSchema` at `app.ts:76-79`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/api.test.ts -t "sets idea readiness over HTTP"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/app.ts tests/api.test.ts
git commit -m "feat: add POST /api/ideas/:id/readiness"
```

---

### Task 8: MCP tool `set_idea_readiness`

**Files:**
- Modify: `src/server/mcpTools.ts:46-49` (add handler after `set_card_readiness`)
- Modify: `src/server/mcp.ts:176-189` (register tool after `set_card_readiness`)

- [ ] **Step 1: Add the handler**

In `src/server/mcpTools.ts`, after the `set_card_readiness` handler (ends line 49) add:

```ts
  async set_idea_readiness(input: { id: string } & SetCardReadinessInput) {
    const { id, ...changes } = input;
    return { item: await store.setIdeaReadiness(access, id, changes) };
  },
```

(`SetCardReadinessInput` is already imported at `mcpTools.ts:6`.)

- [ ] **Step 2: Register the tool**

In `src/server/mcp.ts`, immediately after the `set_card_readiness` registration block (ends line 189) add:

```ts
  server.registerTool(
    "set_idea_readiness",
    {
      title: "Set Idea Readiness",
      description:
        "Record an LLM-evaluated readiness score (1-10) with a short reason on an idea; mirrors onto its linked board card. Does not change updated_at, so a later content edit marks the score stale.",
      inputSchema: {
        id: z.string().min(1),
        score: z.number().int().min(1).max(10),
        reason: z.string().min(1)
      }
    },
    async (input) => jsonContent(await handlers.set_idea_readiness(input))
  );
```

- [ ] **Step 3: Typecheck**

Run: `npm run typecheck`
Expected: PASS.

- [ ] **Step 4: Run the MCP test suite (smoke)**

Run: `npx vitest run tests/mcp-auth.test.ts`
Expected: PASS (no regressions; the new tool registers alongside the others).

- [ ] **Step 5: Commit**

```bash
git add src/server/mcp.ts src/server/mcpTools.ts
git commit -m "feat: add set_idea_readiness MCP tool"
```

---

### Task 9: Client staleness helper (`isCardReadinessStale`)

**Files:**
- Modify: `src/client/taskCardUtils.ts:26-29`
- Test: `tests/task-card-utils.test.ts`

- [ ] **Step 1: Write the failing test**

In `tests/task-card-utils.test.ts`, add the import and a test. Change line 3 import to include the new helper:

```ts
import { isCardReadinessStale, isReadinessStale, readinessColor, taskListCardView } from "../src/client/taskCardUtils.js";
```

Add inside `describe("task card utilities", ...)`:

```ts
  it("computes card staleness from the linked idea when present", () => {
    const ideaById = new Map([["idea-1", { updatedAt: "2026-07-14T12:00:00.000Z" }]]);

    // Card edited earlier than the idea; idea edited AFTER the score -> stale from the idea.
    const card = {
      updatedAt: "2026-07-14T09:00:00.000Z",
      readinessEvaluatedAt: "2026-07-14T10:00:00.000Z",
      ideaId: "idea-1"
    };
    expect(isCardReadinessStale(card, ideaById)).toBe(true);

    // No linked idea -> falls back to the card's own updatedAt (fresh here).
    const orphan = {
      updatedAt: "2026-07-14T09:00:00.000Z",
      readinessEvaluatedAt: "2026-07-14T10:00:00.000Z"
    };
    expect(isCardReadinessStale(orphan, ideaById)).toBe(false);

    // No score -> never stale.
    expect(isCardReadinessStale({ updatedAt: "x", ideaId: "idea-1" }, ideaById)).toBe(false);
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/task-card-utils.test.ts -t "computes card staleness from the linked idea"`
Expected: FAIL — `isCardReadinessStale` is not exported.

- [ ] **Step 3: Add the helper**

In `src/client/taskCardUtils.ts`, after `isReadinessStale` (ends line 29) add:

```ts
export const isCardReadinessStale = (
  card: { updatedAt: string; readinessEvaluatedAt?: string; ideaId?: string },
  ideaById: Map<string, { updatedAt: string }>
): boolean => {
  if (card.readinessEvaluatedAt === undefined) {
    return false;
  }
  const linkedIdea = card.ideaId ? ideaById.get(card.ideaId) : undefined;
  const contentUpdatedAt = linkedIdea?.updatedAt ?? card.updatedAt;
  return contentUpdatedAt > card.readinessEvaluatedAt;
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/task-card-utils.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/client/taskCardUtils.ts tests/task-card-utils.test.ts
git commit -m "feat: add idea-aware card readiness staleness helper"
```

---

### Task 10: Client UI — idea list badge, modal Confidence, card staleness from idea

**Files:**
- Modify: `src/client/main.tsx` — idea derivations (~line 434), idea list card (~1105-1116), board tile badge (1223-1247), detail modal Confidence section (the section added earlier, ~1397).

- [ ] **Step 1: Build an `ideaById` map near the other derivations**

In `src/client/main.tsx`, after `const taskViewCard = taskViewModal?.card ?? null;` (added earlier, ~line 434) add:

```ts
  const ideaById = new Map(state.ideas.map((idea) => [idea.id, idea]));
```

Ensure `isCardReadinessStale` is imported from `./taskCardUtils` alongside `isReadinessStale`/`readinessColor` (update the existing import line).

- [ ] **Step 2: Add the idea-list readiness badge**

In the idea list card, inside the `<Box className="task-card-title">` block, after `{labelBadges(taskCard.labels)}` (line 1113) add:

```tsx
                        {idea.readinessScore !== undefined && (
                          <Badge
                            variant="light"
                            color={isReadinessStale(idea) ? "gray" : readinessColor(idea.readinessScore)}
                            size="sm"
                            opacity={isReadinessStale(idea) ? 0.6 : 1}
                            mt="xs"
                          >
                            {idea.readinessScore}/10{isReadinessStale(idea) ? " · stale" : ""}
                          </Badge>
                        )}
```

(`idea` is the loop variable at `main.tsx:1082`; `isReadinessStale` accepts the idea directly since ideas bump `updatedAt` on edits.)

- [ ] **Step 3: Point the board tile badge staleness at the idea**

In the board column card (`main.tsx:1223-1247`), replace the two `isReadinessStale(card)` calls (lines 1227 and 1241) with `isCardReadinessStale(card, ideaById)`. Leave `readinessColor(card.readinessScore)` unchanged.

- [ ] **Step 4: Make the modal Confidence section read the idea**

Replace the modal Confidence section added earlier (the `taskViewCard?.readinessScore !== undefined && (...)` Paper block, ~line 1397) with an idea-sourced version:

```tsx
            {taskViewIdea?.readinessScore !== undefined && (
              <Paper className="overview-section" withBorder radius="md" p="md">
                <Group align="center" justify="space-between" mb="xs">
                  <Text size="xs" fw={700} tt="uppercase" c="dimmed">
                    Confidence
                  </Text>
                  <Badge
                    variant="light"
                    color={isReadinessStale(taskViewIdea) ? "gray" : readinessColor(taskViewIdea.readinessScore)}
                    opacity={isReadinessStale(taskViewIdea) ? 0.6 : 1}
                  >
                    {taskViewIdea.readinessScore}/10{isReadinessStale(taskViewIdea) ? " · stale" : ""}
                  </Badge>
                </Group>
                {taskViewIdea.readinessReason && (
                  <Text className="overview-text">{taskViewIdea.readinessReason}</Text>
                )}
                {taskViewIdea.readinessEvaluatedAt && (
                  <Text size="xs" c="dimmed" mt="xs">
                    {isReadinessStale(taskViewIdea) ? "Idea changed after evaluation on " : "Evaluated "}
                    {new Date(taskViewIdea.readinessEvaluatedAt).toLocaleString()}
                  </Text>
                )}
              </Paper>
            )}
```

- [ ] **Step 5: Typecheck + full test run**

Run: `npm run typecheck && npm test`
Expected: PASS.

- [ ] **Step 6: Visual check**

Run the UI (host): `npm run dev:ui`. Verify: an idea with a score shows a badge in the idea list; opening the idea detail modal shows the Confidence section; a scored card's badge reflects the idea's staleness.

- [ ] **Step 7: Commit**

```bash
git add src/client/main.tsx
git commit -m "feat: show idea confidence in list + detail modal, card staleness from idea"
```

---

### Task 11: Migrate existing card scores onto their ideas

This is a one-off run against the live board via the MCP tool (no code). Do it after the code above is deployed.

- [ ] **Step 1: List the board and collect scored cards**

Call `list_board`. For each card with a `readinessScore` and an `ideaId`, note `ideaId`, `readinessScore`, `readinessReason`.

- [ ] **Step 2: Copy each score onto its idea**

For each, call `set_idea_readiness` with `{ id: <ideaId>, score: <readinessScore>, reason: <readinessReason> }`. `setIdeaReadiness` writes the idea and mirrors the same values back onto the card.

Known cards at plan time (verify against live `list_board` before running — content edits may have changed them):
- Local skill dir install — score 9
- Tool-call analytics dashboard — score 6
- Cache efficiency view — score 5

- [ ] **Step 3: Verify**

Call `list_ideas` and confirm each migrated idea now carries the score; call `list_board` and confirm the cards still show the same numbers.

---

## Self-Review

**Spec coverage:**
- Data model → Task 1. Schema → Task 2. Row mapping → Task 3. `setIdeaReadiness` (both stores, mirror, no `updated_at` bump) → Task 4. Card-scoring redirect → Task 5. Carry-over on promotion → Task 6. HTTP API → Task 7. MCP tool → Task 8. Client staleness rule → Task 9. UI (idea badge, modal Confidence, card staleness from idea) → Task 10. Migration → Task 11. All spec sections covered.

**Placeholder scan:** No TBD/TODO; every code step shows complete code.

**Type consistency:** `setIdeaReadiness(access, id, input)` (interface/Postgres) vs `setIdeaReadiness(id, input)` (in-memory) matches the existing `setCardReadiness` split. `isCardReadinessStale(card, ideaById)` signature is consistent between Task 9 (definition) and Task 10 (use). Readiness field names (`readinessScore`/`readinessReason`/`readinessEvaluatedAt`) match `types.ts` and the row mappers throughout.

**Open verification points (call out during execution):**
- Postgres-backed tests (Tasks 2, 7) require a test database (`tests/helpers/postgres.ts`). If unavailable, implement the change and note the unverified test.
- Confirm creation status codes in `api.test.ts` (Task 7) match neighboring tests.
