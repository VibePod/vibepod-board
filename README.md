# vibepod-board

Planning board for organizing projects, refining tasks, marking ready work onto a Kanban board, and storing detailed notes or execution plans. The same state is exposed through the browser UI, REST API, and an MCP Streamable HTTP endpoint for Claude Code, Codex, and other MCP clients.

## Stack

- **Backend** (`backend/`): Python 3.12, [FastAPI](https://fastapi.tiangolo.com/) for the REST API, [FastMCP](https://gofastmcp.com/) for the MCP endpoint, [SQLModel](https://sqlmodel.tiangolo.com/) on SQLAlchemy 2 with psycopg 3 for PostgreSQL, and [Alembic](https://alembic.sqlalchemy.org/) for migrations. Managed with [uv](https://docs.astral.sh/uv/).
- **Client** (`src/client/`): React 18, Mantine 8, built with Vite and served by the backend.
- **Storage**: PostgreSQL 16.

The OpenAPI schema is served at `/openapi.json`, with interactive docs at `/docs`.

## Run with Docker

```bash
docker compose up --build
```

Open `http://localhost:3000` on the host running Docker, or `http://<machine-ip>:3000` from another machine on the same network.

State is stored in PostgreSQL in the `vibepod-board-postgres-data` Docker volume. Compose starts both the board service and a `postgres:16-alpine` service.

Set admin credentials before starting the board:

```bash
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
docker compose up --build
```

Admins sign in through the browser UI and can create project-scoped API/MCP tokens from **API Tokens**.

The backend applies database migrations on startup. Set `ALEMBIC_AUTO_UPGRADE=false` to run them yourself with `uv run alembic upgrade head` from `backend/`. A database created by the earlier TypeScript server is adopted in place: it is brought up to the baseline revision and stamped, without recreating any data.

The container joins the shared VibePod Docker network named `vibepod-network` by default. See [docs/integration-guide.md](docs/integration-guide.md) for MCP and VibePod container wiring.

## GitHub Issue Sync

Tasks can be linked to a GitHub issue. The link, the issue state (open/closed) and the last sync time are stored on the task and shown as a badge that opens the issue in a new tab.

- **Link by hand:** paste the issue URL into **GitHub Issue URL** in the task dialog (empty unlinks), or send `githubIssueUrl` with `POST /api/ideas`/`PATCH /api/ideas/:id` or the `update_idea` MCP tool.
- **Push / Pull buttons** in the task view (also `POST /api/ideas/:id/github/push|pull`, MCP `push_github_issue`/`pull_github_issue`):
  - Push creates the issue for an unlinked task, otherwise updates its title, body and labels. It refuses (`409`) when the issue changed on GitHub since the last sync — pull first. It never opens or closes the issue.
  - Pull refreshes title, labels and state. The issue body only fills empty task details, so local refinement is kept. Task status and board column never change.
- **Agent import:** the `upsert_github_issue` MCP tool creates a task for an issue the agent fetched, or refreshes the task already linked to that repository and number. One issue links to at most one task per project.

Push and Pull need a token on the server:

```bash
GITHUB_TOKEN=ghp_xxx            # fine-grained token with Issues read/write
GITHUB_REPOSITORY=owner/repo    # optional default; a task's GitHub remote URL wins
```

## API

- `GET /api/health`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET /api/projects`
- `POST /api/projects`
- `PATCH /api/projects/:id`
- `GET /api/projects/:id/export`
- `POST /api/projects/import`
- `GET /api/ideas`
- `POST /api/ideas`
- `POST /api/ideas/batch`
- `GET /api/ideas/:id`
- `PATCH /api/ideas/:id`
- `DELETE /api/ideas/:id`
- `POST /api/ideas/:id/ready`
- `POST /api/ideas/:id/dependencies`
- `PUT /api/ideas/:id/dependencies`
- `DELETE /api/ideas/:id/dependencies/:dependsOnId`
- `GET /api/work-order`
- `POST /api/ideas/:id/github/push`
- `POST /api/ideas/:id/github/pull`
- `GET /api/github`
- `GET /api/board`
- `POST /api/board/batch`
- `GET /api/board/:id`
- `PATCH /api/board/:id`
- `GET /api/board/archived`
- `POST /api/board/archive-done`
- `POST /api/board/:id/archive`
- `POST /api/board/:id/unarchive`
- `POST /api/board/:id/readiness`
- `POST /api/board/claim`
- `POST /api/board/:id/renew`
- `POST /api/board/:id/handover`
- `POST /api/board/:id/release`
- `GET /api/ideas/:id/history`
- `POST /api/ideas/:id/readiness`
- `GET /api/ideas/:id/readiness`
- `GET /api/readiness`
- `GET /api/activity`
- `GET /api/documents`
- `POST /api/documents`
- `PATCH /api/documents/:id`
- `GET /api/tokens`
- `POST /api/tokens`
- `PATCH /api/tokens/:id`
- `POST /api/tokens/:id/revoke`
- `GET /api/mcp-info`

`GET /api/ideas`, `GET /api/board`, `GET /api/board/archived`, `GET /api/documents`, and `GET /api/work-order` accept `projectId` query params for project-scoped reads.

Browser/full REST access uses the admin session cookie. Project-scoped API clients use `Authorization: Bearer <token>`.

## Project Import and Export

Admins can download a project's data with **Export** on its project card and
upload a project bundle with **Import Project** on the Projects screen. Bundles
include the project, tasks and dependencies, board cards, readiness history,
and documents. API tokens and global activity history are not copied, and neither is
automation state: claims, failed attempts, blocks and the task history stay on the board they
happened on, so imported cards start unclaimed and unblocked.

Imports match projects by their 1–3 letter project key. A new key creates a new
project. An existing key requires explicit confirmation and replaces that
project's current data in one transaction. Replacement retains the destination
project's internal ID, so its API-token assignments remain intact. Project
bundle uploads are limited to 10 MiB.

## MCP

Endpoint: `POST /mcp`

MCP requires:

```text
Authorization: Bearer <project-token>
```

Tools:

- `list_projects`
- `create_project`
- `list_ideas`
- `get_idea`
- `get_board_card`
- `create_idea`
- `update_idea`
- `update_ideas`
- `delete_idea`
- `mark_idea_ready`
- `add_idea_dependency`
- `remove_idea_dependency`
- `set_idea_dependencies`
- `list_work_order`
- `list_board`
- `move_board_card`
- `update_board_card`
- `update_board_cards`
- `archive_board_card`
- `unarchive_board_card`
- `list_archived_cards`
- `set_card_readiness`
- `set_idea_readiness`
- `claim_next_task`
- `renew_task_claim`
- `hand_over_task`
- `release_task`
- `list_task_history`
- `list_idea_readiness`
- `list_readiness`
- `push_github_issue`
- `pull_github_issue`
- `upsert_github_issue`
- `create_document`
- `update_document`
- `list_documents`

Resource:

- `vibepod-board://state`

### References

Every identifier argument accepts, in this order:

1. the record's id;
2. a task key such as `VP-236`, or a project key such as `VP` (case-insensitive);
3. a bare task number such as `236`, when exactly one project is in scope.

A project also resolves by its exact title. `dependsOn` arguments resolve the
same way. A reference that matches nothing returns `404`; one that matches more
than one project returns `400` and names the candidates. A task or card outside
the caller's projects is reported as not found rather than forbidden, so scope
cannot be probed by comparing the two answers; naming a project outside them is
refused (`403`).

### Views

Reads and writes accept `view`, one of:

- `ref` — `id`, `key` and `updatedAt`;
- `compact` — identity, state and relationships, with dependencies named by key
  and `detailsLength` / `acceptanceCriteriaCount` in place of the free text;
- `full` — the whole record.

MCP lists default to `compact` and MCP writes echo `compact`; single reads
default to `full`. REST defaults to `full` everywhere, so the browser is
unaffected, and accepts `?view=compact` on `GET /api/ideas`, `GET /api/ideas/:id`,
`GET /api/board/:id`, `GET /api/documents` and the batch endpoints. Full records
also carry the task's `key`.

### Filters and paging

`list_ideas`, `list_board` and `list_documents` filter by project, by
`status` / `column` / `kind`, by `assignee` / `unassigned`, and by
`updatedSince` (exclusive ISO timestamp).
`list_ideas` (REST: `GET /api/ideas`) accepts `limit` and `cursor` and returns
`nextCursor` when more rows remain; paging is keyset over `(updatedAt, id)`. The
MCP tool caps at 200 records by default, REST is unlimited unless a limit is
given. `GET /api/readiness` (MCP: `list_readiness`) returns the latest score per
task for a project or for named `tasks`, or the full history with
`latestOnly=false`.

### Assignees

Tasks and board cards carry an `assignee`: free text naming whoever holds the
work, by convention an agent naming itself, such as
`Claude::Subagent101::Worktree12`. No format is enforced, and an empty string
releases the task.

- Write it with `POST /api/ideas`, `PATCH /api/ideas/:id`, `PATCH /api/board/:id`
  or the `create_idea`, `update_idea` and `update_board_card` MCP tools.
- The task owns the holder and its card mirrors it. A claim made on the card
  writes through to the task, so a later task edit cannot sync it away.
- Filter with `assignee` (exact match, repeat or comma-separate for several) and
  `unassigned`. Given together they widen rather than narrow: the result is work
  held by one of those holders *or* held by nobody, which is the question an
  agent looking for something to pick up asks.
- To claim without racing another agent, read the task and write `assignee`
  with the `expectedUpdatedAt` you read; a competing claim is then refused
  rather than overwritten.
- The web UI carries the holder as a badge on both the task list card and the
  board card, and an **Assignee** filter above the task list and on the board
  narrows either view to one holder or to unclaimed work. The filter offers only
  the holders present in the project, and one choice serves both views.

### Batch writes

`update_ideas` and `update_board_cards` (REST: `POST /api/ideas/batch`,
`POST /api/board/batch`) apply up to 50 writes in one transaction. A batch is
all-or-nothing: the first failing item rolls the whole batch back and names its
position and reference. One activity row is written per batch.

### Concurrency

Task and card writes accept `expectedUpdatedAt`. When set, the write is refused
if the record changed since that timestamp, and the error names the current one
so a caller can retry in one step (REST: `409`). Readiness writes deliberately
do not move `updatedAt`, because the staleness badge compares content time
against evaluation time; dependency writes do.

## Automated Runners

An automated runner, such as `vp board work` from vibepod-cli, takes planned work off the board
by *claiming* it. Claims are available over REST and MCP, limited to the caller's projects.

- **Claim** with `POST /api/board/claim` (`claim_next_task`), naming the project and the
  `assignee` that claims. The board picks the first task in the work order whose card is in
  **Planned** and that nobody holds, that is not blocked, and whose dependencies are done, and
  moves its card to **In Progress** in the same step. The claimed-by value is the task's
  assignee, so the card shows who took it, and `claimedAt` says since when. Narrow the choice
  with `labels` (the task must carry all of them, case-insensitive) or `minReadiness` (its
  latest readiness score must be at least this), or name one `task`. When nothing can be
  claimed the answer is `{"claimed": false, "reason": ...}`. Claims in one project are
  serialised, so two runners claiming at the same time never get the same task.
- **Lease.** A claim expires at `claimExpiresAt`, `leaseSeconds` after it was taken or last
  renewed (`POST /api/board/:id/renew`, `renew_task_claim`). An expired claim puts the task
  back in Planned and counts a failed attempt. Renewing does not change `updatedAt`.
- **Hand over** with `POST /api/board/:id/handover` (`hand_over_task`): the card moves to
  **Review** with its `branchName`, the claim ends and the failed attempts are cleared.
- **Release** with `POST /api/board/:id/release` (`release_task`) and a `note`, by `outcome`:
  `failed` (the default) returns the task to Planned and counts an attempt; `blocked` blocks
  it with the note as its reason; `released` returns it without counting anything.
- **Blocked tasks.** The attempt that reaches the limit blocks the task instead of returning
  it. A blocked card stays in **Planned** with its reason shown on the card and is skipped by
  claims. Putting it in Planned again by hand — **Unblock** on the card, or any column write of
  `planned` — unblocks it and resets the attempt count.
- **Holders.** Renew, hand over and release name the `assignee` that holds the claim; anyone
  else is refused (`409`). Moving a claimed card by hand, or naming another assignee, ends the
  claim.
- **History.** Every claim, hand-over, failed attempt, block, expiry and unblock is recorded
  in the task history, shown in the task view and listed by `GET /api/ideas/:id/history`
  (`list_task_history`).

Server settings:

```bash
CLAIM_LEASE_SECONDS=900   # default lease when a claim names none
CLAIM_MAX_ATTEMPTS=3      # failed attempts before a task is blocked; a release may pass maxAttempts
CLAIM_SWEEP_SECONDS=15    # how often expired claims are swept; 0 turns the sweep off
```

## Deleting Tasks

**Delete** in the task view or the Edit Task dialog removes a task for good after a confirmation (also `DELETE /api/ideas/:id` and the `delete_idea` MCP tool, both limited to the caller's projects). Its board card, dependency links and readiness history go with it, documents stop linking to it, and tasks that depended on it lose that dependency. A linked GitHub issue is not touched, and task numbers are never reused. To reject work but keep the record, set the task's status to **Denied** instead.

## Archiving Done Cards

Cards in the **Done** column can be archived so the column does not grow forever: **Archive** on a done card, **Archive all done** on the Done column header (after a confirmation), `POST /api/board/:id/archive`, `POST /api/board/archive-done` with `{"projectId": ...}`, or the `archive_board_card` MCP tool. Cards in other columns are rejected (`409`).

Archived cards leave the board (`GET /api/board`, `list_board`) and are listed in the project's **Archive** view (`/projects/:id/archive`), by `GET /api/board/archived`, and by the `list_archived_cards` MCP tool. The task keeps its record, dependencies, readiness history and GitHub link, and still counts as done for the tasks that depend on it. Task edits and GitHub pulls keep refreshing an archived card, but never bring it back; an archived card cannot be moved or edited, and its task cannot be taken off the board. **Unarchive** (`POST /api/board/:id/unarchive`, `unarchive_board_card`) returns it to Done.

Project exports are bundle version 3 and carry each card's `archivedAt`; version 1 and 2 bundles still import.

## Task Dependencies

A task can depend on other tasks in the same project. Dependencies drive the execution order that agents and the UI consume.

- Set them with `POST /api/ideas`/`PATCH /api/ideas/:id` (`dependsOn` replaces the full set), with the dedicated `dependencies` endpoints, or with the `*_idea_dependency` MCP tools. Blockers may be named by key, such as `VP-236`.
- Every task carries `dependsOn` (its blockers), `blocks` (tasks waiting on it), and `blockedBy` (blockers that are not finished yet). Board cards mirror `dependsOn` and `blockedBy` from their task.
- A blocker counts as finished when its board card reached the `done` column, or when the task was denied — denied work never arrives and must not wedge the graph.
- Dependencies must stay inside one project, and cycles are rejected (`409`).
- `GET /api/work-order` and the `list_work_order` MCP tool return tasks in dependency-resolved order. Each item reports `position`, `wave` (0 = no dependencies), `blockedBy`, `isBlocked`, `isComplete`, and `isActionable`; anything listed in `cyclicTaskIds` could not be ordered.

Blocked work is flagged, never forced: moving a blocked card on the board is always allowed.

### Dependency Tree View

The task view has a **List / Tree** switch. Tree draws the dependency graph of the tasks currently visible: blockers sit left of the tasks waiting for them, one lane per dependency wave (wave 0 is unblocked work). Solid orange edges still block; dashed grey edges point out of a blocker that is done or denied. Clicking a node opens that task.

Arrows into and out of a task fan out over its side, so several dependencies on one task stay tellable apart. Click an arrow (or focus it and press Enter) to follow a single dependency: the two tasks it connects stay lit while everything else fades. Escape, a click anywhere else, or a second click on the same arrow brings the full graph back.

The graph follows the search, status, and label filters. A dependency on a task the filters removed is not drawn but counted on the node instead, so nothing silently disappears. Tasks in a dependency cycle cannot be ordered and are grouped in a trailing **Cycle** lane.

## Local Development

Requirements: Node 22, [uv](https://docs.astral.sh/uv/), and a PostgreSQL database.

```bash
npm install
(cd backend && uv sync)
```

Run the API (with reload) and the Vite UI in two terminals; Vite proxies `/api` and `/mcp` to the API on port 3000:

```bash
DATABASE_URL=postgres://vibepod:vibepod@localhost:5432/vibepod_board \
ADMIN_USERNAME=admin \
ADMIN_PASSWORD=admin \
npm run dev
npm run dev:ui
```

To serve a production build of the UI from the API instead, run `npm run build` first; the API serves `dist/client`.

Schema changes go through Alembic: edit the models in `backend/vibepod_board/tables.py`, then generate a revision with `uv run alembic revision --autogenerate -m "..."` from `backend/`.

## Verification

```bash
npm test
npm run typecheck
npm run build
npm run test:api          # pytest; needs initdb/pg_ctl on PATH (or PG_BIN), or TEST_DATABASE_URL
(cd backend && uv run ruff check . && uv run ruff format --check .)
docker build -t vibepod-board:dev .
```

The backend tests start a throwaway PostgreSQL cluster with `initdb`/`pg_ctl`. Point `PG_BIN` at a PostgreSQL `bin` directory when those tools are not on `PATH`, or set `TEST_DATABASE_URL` to use an existing database (its `public` schema is dropped between tests).
