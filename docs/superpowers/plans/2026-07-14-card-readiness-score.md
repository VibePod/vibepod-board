# Board Card Readiness Score Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an LLM-evaluated readiness score (integer 1–10, with rationale) to board cards, set by external agents via a new MCP tool / REST endpoint and displayed as a badge in the board UI, with computed staleness.

**Architecture:** Three optional fields on `BoardCard` (`readinessScore`, `readinessReason`, `readinessEvaluatedAt`). A dedicated `setCardReadiness` store operation stamps `readiness_evaluated_at` server-side and deliberately does NOT touch `updated_at`; staleness is then computed as `updatedAt > readinessEvaluatedAt`. Exposed via `POST /api/board/:id/readiness` and MCP tool `set_card_readiness`. The board never calls an LLM — an external agent evaluates and writes the score.

**Tech Stack:** TypeScript, Express 5, zod, pg (Postgres), MCP SDK, React 18 + Mantine 8, vitest + supertest.

**Spec:** `docs/superpowers/specs/2026-07-14-card-readiness-score-design.md`

**Working directory:** `/workspace/public/vibepod-board`

**Test prerequisite:** Tests use a Postgres test database (see `tests/helpers/postgres.ts`). Run `npm test` once before starting; if it fails on DB connection, start the compose stack first (`docker compose up -d` per `compose.yml`).

**Commit policy:** Commit messages are normal prose (no caveman). Never commit anything under `docs/superpowers/`.

---

### Task 1: Shared types + Postgres schema columns

**Files:**
- Modify: `src/shared/types.ts` (BoardCard ~line 36, after `UpdateBoardCardInput` ~line 124)
- Modify: `src/server/db.ts` (migration block at end of `schemaSql`, ~line 98)
- Test: `tests/postgres-schema.test.ts`

- [ ] **Step 1: Write the failing schema test**

Append inside `describe("PostgreSQL schema", ...)` in `tests/postgres-schema.test.ts`, modeled on the existing "stores branch names on board cards" test:

```ts
  it("stores readiness fields on board cards", async () => {
    await initializeDatabase(pool);

    const columns = await pool.query<{ column_name: string }>(
      `select column_name
       from information_schema.columns
       where table_schema = 'public' and table_name = 'board_cards'
       order by ordinal_position`
    );

    const names = columns.rows.map((row) => row.column_name);
    expect(names).toContain("readiness_score");
    expect(names).toContain("readiness_reason");
    expect(names).toContain("readiness_evaluated_at");
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/postgres-schema.test.ts -t "readiness"`
Expected: FAIL — `names` does not contain `readiness_score`.

- [ ] **Step 3: Add schema columns and shared types**

In `src/server/db.ts`, extend the `alter table` block at the bottom of `schemaSql` (after the `repository_remote_url` lines):

```sql
alter table board_cards add column if not exists readiness_score integer;
alter table board_cards add column if not exists readiness_reason text;
alter table board_cards add column if not exists readiness_evaluated_at timestamptz;
```

In `src/shared/types.ts`, extend `BoardCard` (add after `labels: string[];`):

```ts
  readinessScore?: number;
  readinessReason?: string;
  readinessEvaluatedAt?: string;
```

And add a new input type directly after `UpdateBoardCardInput`:

```ts
export type SetCardReadinessInput = {
  score: number;
  reason: string;
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/postgres-schema.test.ts`
Expected: PASS (all tests in file).

- [ ] **Step 5: Typecheck and commit**

Run: `npm run typecheck`
Expected: clean.

```bash
git add src/shared/types.ts src/server/db.ts tests/postgres-schema.test.ts
git commit -m "Add readiness fields to board card schema and types"
```

---

### Task 2: `setCardReadiness` on PostgresBoardStore + store interface

**Files:**
- Modify: `src/server/store.ts` (interface `BoardDataStore`, ~line 62)
- Modify: `src/server/storage.ts` (`BoardCardRow` ~line 128, `boardCardFromRow` ~line 1174, `PostgresBoardStore` after `moveBoardCard` ~line 589)
- Test: `tests/storage.test.ts`

- [ ] **Step 1: Write the failing tests**

Append inside `describe("PostgresBoardStore", ...)` in `tests/storage.test.ts`. Cards are created by marking an idea ready (same pattern as the existing branch-name test):

```ts
  it("sets readiness on a board card without bumping updated_at", async () => {
    const project = await store.createProject({ key: "APP", title: "App" });
    const idea = await store.createIdea(admin, { projectId: project.id, title: "Scored card" });
    await store.markIdeaReady(admin, idea.id);
    const card = (await store.getBoardColumns(admin, project.id)).ready[0];

    const scored = await store.setCardReadiness(admin, card.id, {
      score: 7,
      reason: "Clear scope, acceptance criteria present"
    });

    expect(scored.readinessScore).toBe(7);
    expect(scored.readinessReason).toBe("Clear scope, acceptance criteria present");
    expect(scored.readinessEvaluatedAt).toBeDefined();
    expect(scored.updatedAt).toBe(card.updatedAt);
    expect(scored.readinessEvaluatedAt! >= scored.updatedAt).toBe(true);
  });

  it("rejects invalid readiness scores and empty reasons", async () => {
    const project = await store.createProject({ key: "APP", title: "App" });
    const idea = await store.createIdea(admin, { projectId: project.id, title: "Card" });
    await store.markIdeaReady(admin, idea.id);
    const card = (await store.getBoardColumns(admin, project.id)).ready[0];

    await expect(store.setCardReadiness(admin, card.id, { score: 0, reason: "r" })).rejects.toThrow(
      "Readiness score must be an integer from 1 to 10"
    );
    await expect(store.setCardReadiness(admin, card.id, { score: 11, reason: "r" })).rejects.toThrow(
      "Readiness score must be an integer from 1 to 10"
    );
    await expect(store.setCardReadiness(admin, card.id, { score: 6.5, reason: "r" })).rejects.toThrow(
      "Readiness score must be an integer from 1 to 10"
    );
    await expect(store.setCardReadiness(admin, card.id, { score: 5, reason: "  " })).rejects.toThrow(
      "Readiness reason is required"
    );
  });

  it("marks readiness stale after a content edit but not after re-evaluation", async () => {
    const project = await store.createProject({ key: "APP", title: "App" });
    const idea = await store.createIdea(admin, { projectId: project.id, title: "Card" });
    await store.markIdeaReady(admin, idea.id);
    const card = (await store.getBoardColumns(admin, project.id)).ready[0];

    const scored = await store.setCardReadiness(admin, card.id, { score: 4, reason: "No repo set" });
    const edited = await store.updateBoardCard(admin, card.id, { details: "Now with repo info" });

    // content edit bumps updatedAt past the evaluation timestamp
    expect(edited.updatedAt > scored.readinessEvaluatedAt!).toBe(true);
    // readiness fields survive the edit
    expect(edited.readinessScore).toBe(4);
    expect(edited.readinessReason).toBe("No repo set");
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run tests/storage.test.ts -t "readiness"`
Expected: FAIL — `store.setCardReadiness is not a function` (and a TypeScript error is fine at this point).

- [ ] **Step 3: Implement interface + Postgres store method**

In `src/server/store.ts`:

Add to the type imports from `"../shared/types.js"`: `SetCardReadinessInput`.

Add to `BoardDataStore` after `moveBoardCard(...)`:

```ts
  setCardReadiness(access: AccessContext, id: string, input: SetCardReadinessInput): Promise<BoardCard>;
```

In `src/server/storage.ts`:

Add `SetCardReadinessInput` to the type imports from `"../shared/types.js"`.

Extend `BoardCardRow` (after `labels: unknown;`):

```ts
  readiness_score: number | null;
  readiness_reason: string | null;
  readiness_evaluated_at: Date | string | null;
```

Extend `boardCardFromRow` (after `labels: jsonStringArray(row.labels),`):

```ts
  readinessScore: row.readiness_score ?? undefined,
  readinessReason: row.readiness_reason ?? undefined,
  readinessEvaluatedAt: row.readiness_evaluated_at ? rowTimestamp(row.readiness_evaluated_at) : undefined,
```

Add a module-level validation helper next to the other helpers (e.g. near `normalizeOptionalText`; it will be reused by the in-memory store in Task 3):

```ts
const normalizeReadinessInput = (input: SetCardReadinessInput): { score: number; reason: string } => {
  if (!Number.isInteger(input.score) || input.score < 1 || input.score > 10) {
    throw new Error("Readiness score must be an integer from 1 to 10");
  }
  const reason = input.reason?.trim() ?? "";
  if (!reason) {
    throw new Error("Readiness reason is required");
  }
  return { score: input.score, reason };
};
```

Add to `PostgresBoardStore` directly after `moveBoardCard` (mirrors its transaction/activity shape; note `updated_at` is intentionally not in the update statement):

```ts
  async setCardReadiness(
    access: AccessContext,
    id: string,
    input: SetCardReadinessInput
  ): Promise<BoardCard> {
    const { score, reason } = normalizeReadinessInput(input);
    const client = await this.pool.connect();
    try {
      await client.query("begin");
      const current = await this.requireBoardCard(client, id);
      assertCanAccessProject(access, current.projectId);
      const timestamp = nowIso();
      const result = await client.query<BoardCardRow>(
        `update board_cards
         set readiness_score = $2,
             readiness_reason = $3,
             readiness_evaluated_at = $4
         where id = $1
         returning *`,
        [id, score, reason, timestamp]
      );
      const card = boardCardFromRow(result.rows[0]);
      await this.addActivity(
        client,
        "board.readiness",
        `Set readiness ${score}/10: ${card.title}`,
        timestamp
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run tests/storage.test.ts`
Expected: PASS. (If the "without bumping updated_at" test flakes because both timestamps land in the same millisecond, the assertions still hold — `updatedAt` equality is exact and `>=` covers the equal case.)

- [ ] **Step 5: Typecheck and commit**

Run: `npm run typecheck`
Expected: clean — the in-memory `BoardStore` class does not implement `BoardDataStore`, so the new interface member only obligates `PostgresBoardStore`.

```bash
git add src/server/store.ts src/server/storage.ts tests/storage.test.ts
git commit -m "Add setCardReadiness to the Postgres board store"
```

---

### Task 3: `setCardReadiness` on the in-memory BoardStore

**Files:**
- Modify: `src/server/storage.ts` (`BoardStore` class, after `updateBoardCard` ~line 1470)
- Test: `tests/board-store-readiness.test.ts` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/board-store-readiness.test.ts`:

```ts
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { BoardStore } from "../src/server/storage.js";

let dir: string;
let store: BoardStore;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "board-readiness-"));
  store = new BoardStore(join(dir, "board.json"));
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

const createCard = () => {
  const project = store.createProject({ key: "APP", title: "App" });
  const idea = store.createIdea({ projectId: project.id, title: "Card" });
  store.markIdeaReady(idea.id);
  return store.getBoardColumns(project.id).ready[0];
};

describe("BoardStore readiness", () => {
  it("sets readiness without bumping updatedAt and keeps it across edits", () => {
    const card = createCard();

    const scored = store.setCardReadiness(card.id, { score: 9, reason: "Fully specified" });
    expect(scored.readinessScore).toBe(9);
    expect(scored.readinessReason).toBe("Fully specified");
    expect(scored.readinessEvaluatedAt).toBeDefined();
    expect(scored.updatedAt).toBe(card.updatedAt);

    const edited = store.updateBoardCard(card.id, { details: "changed" });
    expect(edited.readinessScore).toBe(9);
  });

  it("validates score and reason", () => {
    const card = createCard();

    expect(() => store.setCardReadiness(card.id, { score: 0, reason: "r" })).toThrow(
      "Readiness score must be an integer from 1 to 10"
    );
    expect(() => store.setCardReadiness(card.id, { score: 5, reason: " " })).toThrow(
      "Readiness reason is required"
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/board-store-readiness.test.ts`
Expected: FAIL — `store.setCardReadiness is not a function`.

- [ ] **Step 3: Implement the in-memory method**

In `src/server/storage.ts`, add to the `BoardStore` class directly after `updateBoardCard` (reuses `normalizeReadinessInput` from Task 2; note `card.updatedAt` is intentionally not touched):

```ts
  setCardReadiness(id: string, input: SetCardReadinessInput): BoardCard {
    const { score, reason } = normalizeReadinessInput(input);
    const card = this.requireBoardCard(id);
    card.readinessScore = score;
    card.readinessReason = reason;
    card.readinessEvaluatedAt = nowIso();
    this.addActivity("board.readiness", `Set readiness ${score}/10: ${card.title}`);
    this.save();
    return clone(card);
  }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/board-store-readiness.test.ts`
Expected: PASS.

- [ ] **Step 5: Typecheck and commit**

Run: `npm run typecheck`
Expected: clean.

```bash
git add src/server/storage.ts tests/board-store-readiness.test.ts
git commit -m "Add setCardReadiness to the in-memory board store"
```

---

### Task 4: REST endpoint `POST /api/board/:id/readiness`

**Files:**
- Modify: `src/server/app.ts` (schemas ~line 68, routes after `PATCH /api/board/:id` ~line 279)
- Test: `tests/api.test.ts`

- [ ] **Step 1: Write the failing tests**

Append inside `describe("API", ...)` in `tests/api.test.ts` (uses the existing `createAuthedApp`/`login` helpers; card creation mirrors the existing "promotes a ready idea into the board" flow):

```ts
  it("sets board card readiness through the API", async () => {
    const { app } = createAuthedApp();
    const agent = await login(app);

    const project = await agent.post("/api/projects").send({ title: "App", key: "APP" }).expect(201);
    const idea = await agent
      .post("/api/ideas")
      .send({ projectId: project.body.item.id, title: "Scored" })
      .expect(201);
    await agent.post(`/api/ideas/${idea.body.item.id}/ready`).expect(200);
    const board = await agent.get("/api/board").expect(200);
    const card = board.body.columns.ready[0];

    const response = await agent
      .post(`/api/board/${card.id}/readiness`)
      .send({ score: 8, reason: "Acceptance criteria and repo present" })
      .expect(200);

    expect(response.body.item).toMatchObject({
      id: card.id,
      readinessScore: 8,
      readinessReason: "Acceptance criteria and repo present"
    });
    expect(response.body.item.readinessEvaluatedAt).toBeDefined();
    expect(response.body.item.updatedAt).toBe(card.updatedAt);

    await agent.post(`/api/board/${card.id}/readiness`).send({ score: 42, reason: "r" }).expect(400);
    await agent.post(`/api/board/${card.id}/readiness`).send({ score: 5 }).expect(400);
    await request(app)
      .post(`/api/board/${card.id}/readiness`)
      .send({ score: 5, reason: "r" })
      .expect(401);
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run tests/api.test.ts -t "readiness"`
Expected: FAIL — the readiness POST returns 404 (route does not exist).

- [ ] **Step 3: Implement schema + route**

In `src/server/app.ts`, add next to `updateBoardCardSchema` (~line 68):

```ts
const readinessSchema = z.object({
  score: z.number().int().min(1).max(10),
  reason: z.string().trim().min(1)
});
```

Add the route directly after the `app.patch("/api/board/:id", ...)` block:

```ts
  app.post(
    "/api/board/:id/readiness",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const input = readinessSchema.parse(req.body);
      const item = await store.setCardReadiness(accessFromResponse(req), routeParam(req.params.id), input);
      res.json({ item });
    })
  );
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run tests/api.test.ts`
Expected: PASS.

- [ ] **Step 5: Typecheck and commit**

Run: `npm run typecheck`
Expected: clean.

```bash
git add src/server/app.ts tests/api.test.ts
git commit -m "Add board card readiness endpoint"
```

---

### Task 5: MCP tool `set_card_readiness`

**Files:**
- Modify: `src/server/mcpTools.ts` (handlers object, after `update_board_card` ~line 44)
- Modify: `src/server/mcp.ts` (tool registration, after `update_board_card` ~line 173)
- Test: `tests/mcp-auth.test.ts`

- [ ] **Step 1: Write the failing test**

Append inside `describe("MCP auth", ...)` in `tests/mcp-auth.test.ts` (same setup as the existing branch-name handler test):

```ts
  it("sets card readiness in mapped projects only", async () => {
    const appProject = await store.createProject({ key: "APP", title: "App" });
    const apiProject = await store.createProject({ key: "API", title: "API" });
    const mine = await store.createIdea(adminAccess("admin"), {
      projectId: appProject.id,
      title: "Scored card"
    });
    const theirs = await store.createIdea(adminAccess("admin"), {
      projectId: apiProject.id,
      title: "Off limits card"
    });
    await store.markIdeaReady(adminAccess("admin"), mine.id);
    await store.markIdeaReady(adminAccess("admin"), theirs.id);
    const mineCard = (await store.getBoardColumns(adminAccess("admin"), appProject.id)).ready[0];
    const theirCard = (await store.getBoardColumns(adminAccess("admin"), apiProject.id)).ready[0];

    const handlers = createMcpToolHandlers(store, tokenAccess("token-1", [appProject.id]));

    const scored = await handlers.set_card_readiness({
      id: mineCard.id,
      score: 3,
      reason: "No acceptance criteria, repository unset"
    });
    expect(scored.item).toMatchObject({
      id: mineCard.id,
      readinessScore: 3,
      readinessReason: "No acceptance criteria, repository unset"
    });

    await expect(
      handlers.set_card_readiness({ id: theirCard.id, score: 5, reason: "r" })
    ).rejects.toThrow("Token is not allowed to access project");
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/mcp-auth.test.ts -t "readiness"`
Expected: FAIL — `handlers.set_card_readiness is not a function`.

- [ ] **Step 3: Implement handler + registration**

In `src/server/mcpTools.ts`:

Add `SetCardReadinessInput` to the type imports from `"../shared/types.js"`.

Add to the handlers object after `update_board_card`:

```ts
  async set_card_readiness(input: { id: string } & SetCardReadinessInput) {
    const { id, ...changes } = input;
    return { item: await store.setCardReadiness(access, id, changes) };
  },
```

In `src/server/mcp.ts`, register after the `update_board_card` block (same style):

```ts
  server.registerTool(
    "set_card_readiness",
    {
      title: "Set Card Readiness",
      description:
        "Record an LLM-evaluated readiness score (1-10) with a short reason on a board card. Does not change updated_at, so a later content edit marks the score stale.",
      inputSchema: {
        id: z.string().min(1),
        score: z.number().int().min(1).max(10),
        reason: z.string().min(1)
      }
    },
    async (input) => jsonContent(await handlers.set_card_readiness(input))
  );
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/mcp-auth.test.ts`
Expected: PASS.

- [ ] **Step 5: Typecheck and commit**

Run: `npm run typecheck`
Expected: clean.

```bash
git add src/server/mcpTools.ts src/server/mcp.ts tests/mcp-auth.test.ts
git commit -m "Add set_card_readiness MCP tool"
```

---

### Task 6: Client helpers — `readinessColor` and `isReadinessStale`

**Files:**
- Modify: `src/client/taskCardUtils.ts`
- Test: `tests/task-card-utils.test.ts`

- [ ] **Step 1: Write the failing tests**

Append inside `describe("task card utilities", ...)` in `tests/task-card-utils.test.ts` (add `readinessColor, isReadinessStale` to the existing import from `../src/client/taskCardUtils.js`):

```ts
  it("maps readiness scores to badge colors", () => {
    expect(readinessColor(1)).toBe("red");
    expect(readinessColor(3)).toBe("red");
    expect(readinessColor(4)).toBe("yellow");
    expect(readinessColor(6)).toBe("yellow");
    expect(readinessColor(7)).toBe("green");
    expect(readinessColor(10)).toBe("green");
  });

  it("computes readiness staleness from timestamps", () => {
    const base = {
      updatedAt: "2026-07-14T10:00:00.000Z"
    };
    expect(isReadinessStale({ ...base, readinessEvaluatedAt: undefined })).toBe(false);
    expect(isReadinessStale({ ...base, readinessEvaluatedAt: "2026-07-14T10:00:00.000Z" })).toBe(false);
    expect(isReadinessStale({ ...base, readinessEvaluatedAt: "2026-07-14T11:00:00.000Z" })).toBe(false);
    expect(isReadinessStale({ ...base, readinessEvaluatedAt: "2026-07-14T09:00:00.000Z" })).toBe(true);
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run tests/task-card-utils.test.ts`
Expected: FAIL — `readinessColor` is not exported.

- [ ] **Step 3: Implement the helpers**

Append to `src/client/taskCardUtils.ts`:

```ts
export const readinessColor = (score: number): "red" | "yellow" | "green" => {
  if (score <= 3) {
    return "red";
  }
  if (score <= 6) {
    return "yellow";
  }
  return "green";
};

export const isReadinessStale = (card: {
  updatedAt: string;
  readinessEvaluatedAt?: string;
}): boolean => card.readinessEvaluatedAt !== undefined && card.updatedAt > card.readinessEvaluatedAt;
```

(ISO-8601 UTC strings compare correctly as strings; both timestamps come from the server's `nowIso()`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run tests/task-card-utils.test.ts`
Expected: PASS.

- [ ] **Step 5: Typecheck and commit**

Run: `npm run typecheck`
Expected: clean.

```bash
git add src/client/taskCardUtils.ts tests/task-card-utils.test.ts
git commit -m "Add readiness color and staleness helpers"
```

---

### Task 7: Board card badge in the UI

**Files:**
- Modify: `src/client/main.tsx` (Mantine imports ~line 1; card body render ~line 1219, after the `Title`/`labelBadges` lines)

No new unit test — `main.tsx` has no direct render test for board cards; correctness is covered by the Task 6 helper tests plus typecheck, and verified visually in Task 9.

- [ ] **Step 1: Add imports**

In the `@mantine/core` import list of `src/client/main.tsx`, add `Tooltip` (alphabetical position: after `ThemeIcon`, before `useMantineColorScheme`).

Add to the existing import from `"./taskCardUtils.js"` — or create one if only other utils are imported (check the import block; `taskListCardView` is already imported from this module elsewhere in the file):

```ts
import { isReadinessStale, readinessColor } from "./taskCardUtils.js";
```

- [ ] **Step 2: Render the badge**

Inside the board card `<Stack gap="xs">` (directly after `{labelBadges(card.labels, "xs")}` and before the `card.branchName` block), add:

```tsx
{card.readinessScore !== undefined && (
  <Group className="board-card-readiness" justify="flex-start">
    <Tooltip
      label={
        isReadinessStale(card)
          ? `${card.readinessReason ?? ""} (card changed after evaluation on ${new Date(
              card.readinessEvaluatedAt ?? ""
            ).toLocaleString()})`
          : `${card.readinessReason ?? ""} (evaluated ${new Date(
              card.readinessEvaluatedAt ?? ""
            ).toLocaleString()})`
      }
      multiline
      maw={320}
      withArrow
    >
      <Badge
        variant="light"
        color={isReadinessStale(card) ? "gray" : readinessColor(card.readinessScore)}
        size="sm"
        opacity={isReadinessStale(card) ? 0.6 : 1}
      >
        {card.readinessScore}/10{isReadinessStale(card) ? " · stale" : ""}
      </Badge>
    </Tooltip>
  </Group>
)}
```

- [ ] **Step 3: Typecheck and run the full test suite**

Run: `npm run typecheck && npm test`
Expected: both clean/green.

- [ ] **Step 4: Commit**

```bash
git add src/client/main.tsx
git commit -m "Show readiness badge on board cards"
```

---

### Task 8: Evaluator contract documentation

**Files:**
- Modify: `docs/integration-guide.md` (append a new section; read the file first and match its heading style/level)

- [ ] **Step 1: Append the evaluator section**

Add to `docs/integration-guide.md`:

```markdown
## Readiness evaluation (external agent)

Board cards carry an optional LLM-evaluated readiness score (integer 1-10) with a short
rationale. The board only stores and displays the score - evaluation is done by an external
agent (for example a Claude Code session or a scheduled vibepod task) through MCP.

The evaluation loop:

1. Call `list_board` for the project.
2. Select cards that are unscored (`readinessScore` absent) or stale
   (`updatedAt > readinessEvaluatedAt`; the UI shows these dimmed with a "stale" marker).
3. Score each card 1-10 for **spec completeness** - how ready it is for an agent to pick up:
   - Scope is clearly bounded (what is in, what is out).
   - Acceptance criteria or testable outcomes are stated.
   - Repository and branch information is set.
   - No unresolved decisions (phrases like "TBD" or "decide in PR" lower the score).
4. Call `set_card_readiness` with `{ id, score, reason }` where `reason` is one or two
   sentences naming what is missing (or confirming completeness).

Setting readiness never changes the card's `updated_at`, so a fresh evaluation is never
immediately stale; any later content edit or column move marks the score stale until the
next evaluation pass.
```

- [ ] **Step 2: Commit**

```bash
git add docs/integration-guide.md
git commit -m "Document the readiness evaluation contract"
```

---

### Task 9: Final verification

- [ ] **Step 1: Full suite**

Run: `npm test && npm run typecheck`
Expected: all green, no type errors.

- [ ] **Step 2: End-to-end smoke via the running board**

The live board this session's MCP tools talk to runs this codebase. After deploying/restarting it with the new code (out of band), a smoke test is: call the `set_card_readiness` MCP tool on a real card, then `list_board` and confirm the readiness fields round-trip, and check the badge in the UI. If the running instance cannot be restarted now, verify locally instead: `docker compose up -d` (Postgres) + `npm run dev` + `npm run dev:ui`, log in, score a card via `curl -X POST .../api/board/<id>/readiness`, and confirm the badge renders and dims after editing the card details.

---

## Self-review notes

- Spec coverage: data model (Task 1), server op both stores (Tasks 2-3), REST (Task 4), MCP (Task 5), staleness + UI helpers (Task 6), badge (Task 7), evaluator docs (Task 8), tests throughout. "Out of scope" items untouched.
- Type consistency: `SetCardReadinessInput` defined in Task 1, used in Tasks 2-5 with the same shape; `normalizeReadinessInput` defined in Task 2, reused in Task 3; helper names `readinessColor`/`isReadinessStale` consistent across Tasks 6-7.
- The in-memory `BoardStore` does not implement the `BoardDataStore` interface (pre-existing design); Tasks 2 and 3 handle the two classes separately on purpose.
