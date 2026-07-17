# Board Card Readiness Score — Design

Date: 2026-07-14
Status: approved

## Summary

Add an LLM-evaluated readiness score (integer 1–10) to board cards, measuring **spec completeness**: how ready a card is for an agent to pick up (clear scope, acceptance criteria, repository/branch info, no unresolved decisions). The board stores and displays the score; an **external agent** evaluates and sets it via a new MCP tool. The board itself gains no LLM dependency.

## Decisions

| Question | Decision |
|---|---|
| What the score measures | Spec completeness (agent-pickup readiness), 1–10 |
| Scoped entities | Board cards only (ideas keep existing status flow) |
| Evaluator | External agent via MCP; board stores score only |
| Staleness | Keep score, mark stale when card content changed after evaluation |
| Rationale | Yes — short reason text stored with the score |
| Storage approach | Dedicated fields on `BoardCard` + dedicated `set_card_readiness` tool (no history table, no overload of `update_board_card`) |

## Data model

`src/shared/types.ts` — `BoardCard` gains:

```ts
readinessScore?: number;        // integer 1–10
readinessReason?: string;       // short rationale from the evaluator
readinessEvaluatedAt?: string;  // ISO timestamp, set server-side
```

New input type:

```ts
export type SetCardReadinessInput = {
  score: number;   // integer 1–10, validated server-side
  reason: string;  // required, non-empty after trim
};
```

`src/server/db.ts` — follow the existing idempotent migration pattern:

```sql
alter table board_cards add column if not exists readiness_score integer;
alter table board_cards add column if not exists readiness_reason text;
alter table board_cards add column if not exists readiness_evaluated_at timestamptz;
```

## Server

`src/server/storage.ts` — new `setCardReadiness(id, input)` on **both** implementations (Postgres and in-memory):

- Validates `score` is an integer in 1–10; rejects otherwise (400 at API layer).
- Validates `reason` non-empty after trim.
- Sets `readiness_evaluated_at = now()`.
- **Does not modify `updated_at`** — this is what makes computed staleness work.
- Returns the updated card; throws not-found like existing card operations.
- Records an activity event (e.g. "Readiness of \"<title>\" set to 7/10").

`src/server/app.ts` — new endpoint following existing card-route conventions and auth middleware:

```
POST /api/board/:id/readiness   body: { score, reason }
```

(Mirrors the existing `POST /api/ideas/:id/ready` action-route pattern; card routes live under `/api/board`.)

`src/server/mcp.ts` + `src/server/mcpTools.ts` — new MCP tool:

```
set_card_readiness { id: string, score: number (1–10 int), reason: string }
```

Returns the updated card JSON, same shape as `update_board_card`.

## Staleness

Computed, never stored:

```
stale = readinessEvaluatedAt !== undefined && updatedAt > readinessEvaluatedAt
```

Content edits and column moves bump `updatedAt` (existing behavior), so any change after evaluation marks the score stale. Setting readiness does not bump `updatedAt`, so a fresh evaluation is never self-stale.

Note: column moves also bump `updatedAt`, so moving a card marks its score stale even though the spec text did not change. Accepted for V1 — a column move is a state change the evaluator may legitimately reconsider, and avoiding it would require content hashing (rejected as YAGNI).

## UI

`src/client/taskCardUtils.ts` — pure helpers (unit-testable):

- `readinessColor(score)`: red for 1–3, yellow for 4–6, green for 7–10.
- `isReadinessStale(card)`: implements the staleness rule above.

`src/client/main.tsx` — card rendering:

- Scored card: Mantine `Badge` showing `7/10` in the mapped color, with a `Tooltip` showing the reason and evaluated date.
- Stale score: badge dimmed (reduced opacity / gray variant) plus a stale indicator (e.g. "stale" suffix or clock icon) and tooltip noting the card changed after evaluation.
- Unscored card: no badge.

## Evaluator contract (documentation only)

`docs/integration-guide.md` gains a section describing the loop an external agent runs:

1. `list_board` for the project.
2. Select cards that are unscored, or stale (`updatedAt > readinessEvaluatedAt`).
3. Score each card 1–10 against the rubric: scope clarity, acceptance criteria present, repository/branch info set, no unresolved decisions ("decide in PR", "TBD" reduce the score).
4. Call `set_card_readiness` with score + one-to-two-sentence reason.

Where the agent runs (interactive Claude session, vibepod task, cron) is out of scope for the board.

## Testing

- `tests/storage.test.ts`: set readiness happy path; score validation (0, 11, non-integer rejected); empty reason rejected; `updated_at` unchanged by readiness writes; behavior identical on both storage implementations; not-found error.
- `tests/api.test.ts`: `POST /api/board/:id/readiness` success, validation failure (400), auth required, unknown id (404).
- MCP tool test alongside existing tool tests: `set_card_readiness` round-trip.
- `tests/task-card-utils.test.ts`: `readinessColor` boundaries (3/4, 6/7); `isReadinessStale` for unscored, fresh, and stale cards.

## Out of scope

- Ideas scoring, score history, LLM calls from the board server, evaluator scheduling, filtering/sorting the board by score.
