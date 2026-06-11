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

`GET /api/ideas`, `GET /api/board`, and `GET /api/documents` accept `projectId` query params for project-scoped reads.

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
- `list_board`
- `move_board_card`
- `create_document`
- `update_document`
- `list_documents`

Resource:

- `vibepod-board://state`

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
