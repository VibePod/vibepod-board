# vibepod-board

Planning board for refining ideas into ready work, moving synced work through a Kanban board, and storing detailed execution plans. The same state is exposed through the browser UI, REST API, and an MCP Streamable HTTP endpoint for Claude Code, Codex, and other MCP clients.

## Run with Docker

```bash
docker compose up --build
```

Open `http://localhost:3000` on the host running Docker, or `http://<machine-ip>:3000` from another machine on the same network.

State is stored in the `vibepod-board-data` Docker volume at `/data/board.json`.

The container joins the shared VibePod Docker network named `vibepod-network` by default. See [docs/integration-guide.md](docs/integration-guide.md) for MCP and VibePod container wiring.

## Optional GitHub Issue Sync

Without GitHub environment variables, the sync action runs in local mode: ready ideas become board cards without creating GitHub issues.

To create real GitHub issues, set:

```bash
GITHUB_TOKEN=ghp_xxx
GITHUB_REPOSITORY=owner/repo
```

## API

- `GET /api/health`
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
- `GET /api/mcp-info`

## MCP

Endpoint: `POST /mcp`

Tools:

- `list_ideas`
- `create_idea`
- `mark_idea_ready`
- `list_board`
- `move_board_card`
- `create_document`
- `list_documents`

Resource:

- `vibepod-board://state`

## Local Development

```bash
npm install
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
