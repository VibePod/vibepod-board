# vibepod-board

Planning board for organizing projects, refining tasks, marking ready work onto a Kanban board, and storing detailed notes or execution plans. The same state is exposed through the browser UI, REST API, and an MCP Streamable HTTP endpoint for Claude Code, Codex, and other MCP clients.

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

To import an existing development `board.json` into an empty PostgreSQL database, run:

```bash
npm run import:json -- ./data/board.json
```

The container joins the shared VibePod Docker network named `vibepod-network` by default. See [docs/integration-guide.md](docs/integration-guide.md) for MCP and VibePod container wiring.

## Optional GitHub Issue Sync

Ready tasks become board cards locally. The API sync endpoint can optionally attach a GitHub issue when GitHub environment variables are configured.

To create real GitHub issues, set:

```bash
GITHUB_TOKEN=ghp_xxx
GITHUB_REPOSITORY=owner/repo
```

## API

- `GET /api/health`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET /api/projects`
- `POST /api/projects`
- `PATCH /api/projects/:id`
- `GET /api/ideas`
- `POST /api/ideas`
- `PATCH /api/ideas/:id`
- `POST /api/ideas/:id/ready`
- `POST /api/ideas/:id/dependencies`
- `PUT /api/ideas/:id/dependencies`
- `DELETE /api/ideas/:id/dependencies/:dependsOnId`
- `GET /api/work-order`
- `POST /api/ideas/:id/sync-github`
- `GET /api/board`
- `PATCH /api/board/:id`
- `GET /api/documents`
- `POST /api/documents`
- `PATCH /api/documents/:id`
- `GET /api/tokens`
- `POST /api/tokens`
- `PATCH /api/tokens/:id`
- `POST /api/tokens/:id/revoke`
- `GET /api/mcp-info`

`GET /api/ideas`, `GET /api/board`, `GET /api/documents`, and `GET /api/work-order` accept `projectId` query params for project-scoped reads.

Browser/full REST access uses the admin session cookie. Project-scoped API clients use `Authorization: Bearer <token>`.

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
- `mark_idea_ready`
- `add_idea_dependency`
- `remove_idea_dependency`
- `set_idea_dependencies`
- `list_work_order`
- `list_board`
- `move_board_card`
- `create_document`
- `update_document`
- `list_documents`

Resource:

- `vibepod-board://state`

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

```bash
npm install
DATABASE_URL=postgres://vibepod:vibepod@localhost:5432/vibepod_board \
ADMIN_USERNAME=admin \
ADMIN_PASSWORD=admin \
npm run dev
```

For Vite hot reload during UI work, run the API and UI separately:

```bash
npm run dev
npm run dev:ui
```

## Verification

```bash
npm test
npm run typecheck
npm run build
docker build -t vibepod-board:dev .
```
