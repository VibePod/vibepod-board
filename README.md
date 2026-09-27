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
- `PATCH /api/board/:id`
- `GET /api/board/archived`
- `POST /api/board/archive-done`
- `POST /api/board/:id/archive`
- `POST /api/board/:id/unarchive`
- `POST /api/board/:id/readiness`
- `POST /api/ideas/:id/readiness`
- `GET /api/ideas/:id/readiness`
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
and documents. API tokens and global activity history are not copied.

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
- `create_idea`
- `update_idea`
- `delete_idea`
- `mark_idea_ready`
- `add_idea_dependency`
- `remove_idea_dependency`
- `set_idea_dependencies`
- `list_work_order`
- `list_board`
- `move_board_card`
- `update_board_card`
- `archive_board_card`
- `unarchive_board_card`
- `list_archived_cards`
- `set_card_readiness`
- `set_idea_readiness`
- `list_idea_readiness`
- `push_github_issue`
- `pull_github_issue`
- `upsert_github_issue`
- `create_document`
- `update_document`
- `list_documents`

Resource:

- `vibepod-board://state`

## Deleting Tasks

**Delete** in the task view or the Edit Task dialog removes a task for good after a confirmation (also `DELETE /api/ideas/:id` and the `delete_idea` MCP tool, both limited to the caller's projects). Its board card, dependency links and readiness history go with it, documents stop linking to it, and tasks that depended on it lose that dependency. A linked GitHub issue is not touched, and task numbers are never reused. To reject work but keep the record, set the task's status to **Denied** instead.

## Archiving Done Cards

Cards in the **Done** column can be archived so the column does not grow forever: **Archive** on a done card, **Archive all done** on the Done column header (after a confirmation), `POST /api/board/:id/archive`, `POST /api/board/archive-done` with `{"projectId": ...}`, or the `archive_board_card` MCP tool. Cards in other columns are rejected (`409`).

Archived cards leave the board (`GET /api/board`, `list_board`) and are listed in the project's **Archive** view (`/projects/:id/archive`), by `GET /api/board/archived`, and by the `list_archived_cards` MCP tool. The task keeps its record, dependencies, readiness history and GitHub link, and still counts as done for the tasks that depend on it. Task edits and GitHub pulls keep refreshing an archived card, but never bring it back; an archived card cannot be moved or edited, and its task cannot be taken off the board. **Unarchive** (`POST /api/board/:id/unarchive`, `unarchive_board_card`) returns it to Done.

Project exports are bundle version 3 and carry each card's `archivedAt`; version 1 and 2 bundles still import.

## Task Dependencies

A task can depend on other tasks in the same project. Dependencies drive the execution order that agents and the UI consume.

- Set them with `POST /api/ideas`/`PATCH /api/ideas/:id` (`dependsOn` replaces the full set), with the dedicated `dependencies` endpoints, or with the `*_idea_dependency` MCP tools.
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
