# Idea Confidence Score — Design

Date: 2026-07-17
Repo: `vibepod-board`
Status: approved (pending spec review)

## Goal

Extend the existing board-card "readiness / confidence score" feature to **Ideas** (tasks not yet on the board). The idea becomes the **single source of truth** for a task's confidence score across its whole lifecycle (idea → board card).

## Decisions (from brainstorming)

- **Idea is canonical.** The confidence score is authored on the idea. Board cards mirror their linked idea's score.
- **Cards mirror via write-sync.** The codebase already keeps cards in sync with their idea (`syncBoardCardFromIdea` copies title/details/labels). Readiness follows the same pattern: writing an idea's readiness also writes the linked card's readiness columns. (Not a read-time projection.)
- **Card scoring redirects to the idea.** The existing `set_card_readiness` API + MCP tool, when called on a card that has a linked idea, writes to the idea (which then mirrors back to the card). All cards today are created from an idea, so this covers every card.
- **Readiness writes never bump `updated_at`.** For both the idea and its card, `setIdeaReadiness` updates only the three readiness columns. This preserves the staleness signal (mirrors current card behavior).
- **Staleness tracks the idea.** A score is "stale" when the *idea* was edited after it was scored (`idea.updatedAt > idea.readinessEvaluatedAt`). The client computes a card's staleness from its linked idea, not from `card.updatedAt` (a content-sync bumps the card's `updatedAt` and would otherwise falsely flag stale).
- **Migrate existing data.** The board cards already scored have their score/reason/evaluatedAt copied onto their linked ideas via `set_idea_readiness`; the sync writes the same values back onto the cards.
- **UI surfaces:** idea-list badge + idea-detail modal Confidence section.

## Data Model (`src/shared/types.ts`)

Add three optional fields to `Idea`, mirroring `BoardCard`:

```ts
readinessScore?: number;
readinessReason?: string;
readinessEvaluatedAt?: string;
```

Reuse the existing `SetCardReadinessInput` (`{ score, reason }`) — no new input type needed.

## Storage (`src/server/db.ts`, `src/server/storage.ts`, `src/server/store.ts`)

1. **Schema** (`db.ts`, after the ideas table / alters): mirror the card alters:
   ```
   alter table ideas add column if not exists readiness_score integer;
   alter table ideas add column if not exists readiness_reason text;
   alter table ideas add column if not exists readiness_evaluated_at timestamptz;
   ```
2. **Row mapping**: add the three fields to `IdeaRow` and to `ideaFromRow`.
3. **New method** `setIdeaReadiness(access, id, input)` on `BoardDataStore` (interface in `store.ts`), implemented in both PostgresStore and the in-memory store:
   - Validate via `normalizeReadinessInput`.
   - Update the idea's three readiness columns with `readiness_evaluated_at = now` — **do NOT touch the idea's `updated_at`**.
   - Mirror to the linked card: `update board_cards set readiness_score/reason/evaluated_at where idea_id = $idea` — **do NOT touch the card's `updated_at`**.
   - Return the mapped idea.
4. **Redirect card scoring**: `setCardReadiness(access, id, input)` — resolve the card's `ideaId`; if present, call `setIdeaReadiness(access, ideaId, input)` and return the freshly re-read card. If no `ideaId` (not expected today), fall back to the existing card-local write.
5. **Carry-over on promotion**: `ensureBoardCard` (both stores) copies the idea's `readinessScore`/`readinessReason`/`readinessEvaluatedAt` into the card it inserts/updates, so a card promoted from a scored idea already carries the score.

### Staleness note
The score lives on the idea; the card's readiness columns are a mirror written without bumping `card.updated_at`. The client computes a card's staleness from the **linked idea** (`idea.updatedAt` vs the score's `readinessEvaluatedAt`), because a later content edit bumps both `idea.updatedAt` and (via `syncBoardCardFromIdea`) `card.updatedAt`; using the idea keeps one authoritative rule. A card with no linked idea falls back to card-local staleness.

## HTTP API (`src/server/app.ts`)

Add `POST /api/ideas/:id/readiness`, mirroring `POST /api/board/:id/readiness`:
- Reuse the existing `readinessSchema` (score int 1–10, non-empty reason).
- Call `store.setIdeaReadiness(access, id, input)`, return `{ item }`.

## MCP Tool (`src/server/mcp.ts`, `src/server/mcpTools.ts`)

- Register `set_idea_readiness` with the same input shape as `set_card_readiness` (`id`, `score` int 1–10, `reason`), following the existing `registerTool(...) -> jsonContent(await handlers.NAME(input))` pattern.
- Add the `set_idea_readiness` handler in `createMcpToolHandlers` → `store.setIdeaReadiness(access, id, changes)`.
- `set_card_readiness` remains registered; its handler now benefits from the storage-layer redirect (no MCP change needed there).

## UI (`src/client/main.tsx`, reuse `src/client/taskCardUtils.ts`)

1. **Idea-list badge**: in the idea list render loop (`visibleProjectIdeas.map`), add a score `/10` badge when `idea.readinessScore !== undefined`, reusing `readinessColor` and `isReadinessStale`. `isReadinessStale` already accepts a generic `{ updatedAt, readinessEvaluatedAt }`, so passing the idea works as-is.
2. **Idea-detail modal Confidence section**: the modal Confidence section already added reads `taskViewCard` (the linked card). Change it to prefer the **idea's** readiness (`taskViewIdea`) and compute staleness from the idea. Falls back to nothing when the idea has no score.
3. **Card staleness from idea**: where board-card tiles render the readiness badge (`main.tsx` board column, ~1223-1247) and the modal Confidence section, compute staleness from the linked idea when the card has an `ideaId` (look it up in `state.ideas`), instead of `isReadinessStale(card)`. Add a small helper `isCardReadinessStale(card, ideaById)` to `taskCardUtils.ts` that uses the idea's `updatedAt` when linked, else the card's.

## Migration (one-off)

Copy readiness from the already-scored cards onto their linked ideas:
- For each board card with a `readinessScore` and an `ideaId`, call `set_idea_readiness` with the card's existing score + reason on the linked idea.
- `setIdeaReadiness` writes the idea and mirrors the same values back onto the card, so nothing is lost.
- This is a one-off run via the MCP tool against the live board (no code migration needed).

## Testing

- Storage: `setIdeaReadiness` persists on the idea and mirrors to the linked card, without bumping either `updated_at`; `setCardReadiness` redirects to the idea when `ideaId` is present; `ensureBoardCard` carries idea readiness onto a promoted card.
- API: `POST /api/ideas/:id/readiness` validates score range + non-empty reason; returns the updated idea.
- MCP: `set_idea_readiness` round-trips.
- UI: idea badge renders and flags stale after an idea edit; idea-detail Confidence section renders.

## Out of Scope

- Automatic/LLM re-scoring on content change (only the staleness flag is shown).
- Removing the card-local readiness DB columns (kept for the no-idea fallback and to avoid a destructive migration).
