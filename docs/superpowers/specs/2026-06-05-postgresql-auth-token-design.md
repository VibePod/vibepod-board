# PostgreSQL Storage, Admin Auth, and Project Token Design

## Goal

Move `vibepod-board` from local JSON file persistence to PostgreSQL, add global admin authentication for the browser/full REST API, and add admin-managed bearer tokens that grant API/MCP access to one or more existing projects.

## Current Context

The app currently stores all board state in `/data/board.json` through `BoardStore` in `src/server/storage.ts`. The browser UI, REST API, and MCP endpoint share that store. Projects already exist as the app's scoping boundary, and existing API/MCP operations support project filtering through `projectId` where needed.

Runtime file storage should be removed. Existing development JSON data should remain recoverable through an explicit temporary import script.

## Architecture

PostgreSQL becomes the only runtime persistence layer. The server keeps the current domain operations for projects, ideas, board cards, documents, activity, GitHub sync metadata, and MCP tools, but the implementation reads and writes PostgreSQL instead of loading a JSON document into memory.

The storage boundary should stay narrow. Express and MCP should depend on a store interface with the current domain methods, plus token-management methods. This lets the API and MCP layers remain focused on transport, validation, and authorization instead of SQL details.

Docker Compose adds a `postgres` service and a persistent database volume. The board service connects through `DATABASE_URL`. The board container no longer mounts `/data` for normal runtime persistence.

## PostgreSQL Data Model

Use relational tables for the current board entities:

- `projects`: `id`, `key`, `title`, `summary`, `created_at`, `updated_at`
- `ideas`: `id`, `project_id`, `task_number`, `title`, `summary`, `details`, `status`, `labels`, `acceptance_criteria`, optional GitHub issue fields, timestamps
- `board_cards`: `id`, `project_id`, `idea_id`, `title`, `details`, `column`, optional GitHub issue fields, `labels`, timestamps
- `documents`: `id`, `project_id`, `title`, `kind`, `content`, `linked_idea_ids`, `linked_card_ids`, timestamps
- `activity_events`: `id`, `type`, `message`, `created_at`
- `api_tokens`: `id`, `name`, `token_hash`, `created_at`, `last_used_at`, `revoked_at`
- `api_token_projects`: `token_id`, `project_id`

Arrays can be represented as PostgreSQL `text[]` or `jsonb`; the implementation should choose the simpler shape that keeps current API response types unchanged.

Store only token hashes. Raw token values are shown once when an admin creates a token and are never persisted.

## Admin Authentication

Admin credentials are configured with environment variables:

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

Admins authenticate through the browser using username/password. The server issues an HTTP-only session cookie. Admin sessions can use all existing REST endpoints and all token-management endpoints.

In production, the server must fail startup if either admin variable is missing. In test/development, tests may pass explicit app options or test env values to avoid brittle process-wide configuration.

## Token Authentication and Authorization

API/MCP clients send:

```text
Authorization: Bearer <token>
```

The server hashes the presented token, looks it up in `api_tokens`, rejects unknown or revoked tokens, updates `last_used_at`, and loads the mapped project IDs from `api_token_projects`.

Tokens are mapped to one or more existing projects. They do not create projects, update projects, manage tokens, or access admin-only endpoints.

Project-scoped behavior:

- Read/list operations return only data from allowed projects.
- If a token supplies `projectId`, the request succeeds only when that project is allowed.
- If a token omits `projectId` on a create operation, the server uses the first allowed project as the default.
- If a token has multiple projects and omits `projectId` on a broad read, the response includes data from all allowed projects.
- Moving a board card requires that the card's project is allowed.
- Creating documents or ideas requires the target project to be allowed.

Admin sessions bypass project token scoping because admins have full access.

## REST API Changes

Add auth endpoints:

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`

Add admin-only token endpoints:

- `GET /api/tokens`
- `POST /api/tokens`
- `PATCH /api/tokens/:id`
- `POST /api/tokens/:id/revoke`

Existing REST endpoints should require either an admin session or a valid project token, except `GET /api/health`, `GET /api/mcp-info`, `POST /api/auth/login`, `POST /api/auth/logout`, and `GET /api/auth/me`. `GET /api/auth/me` returns the current admin identity when a valid admin session is present and returns an unauthenticated response otherwise.

Token creation takes a name and one or more project IDs. The response includes token metadata and the raw token only on creation.

## MCP Changes

`POST /mcp` requires `Authorization: Bearer <token>`.

MCP tools receive an authorization context derived from the token and enforce project access consistently with REST. Supported tools remain:

- `list_projects`
- `create_project`
- `list_ideas`
- `create_idea`
- `mark_idea_ready`
- `list_board`
- `move_board_card`
- `create_document`
- `list_documents`

`create_project` is admin-only and should not be available to project tokens unless the request is authenticated as admin. If the MCP endpoint only supports bearer tokens initially, `create_project` should reject project-token requests with an authorization error.

The MCP resource `vibepod-board://state` should return only allowed project data for project tokens.

## Browser UI Changes

When unauthenticated, the UI shows a login screen. After admin login, the current board UI is available.

Add an admin token-management view where admins can:

- List tokens with name, project mappings, creation time, last-used time, and revoked state.
- Create a token by selecting one or more projects.
- See the raw token once after creation.
- Revoke a token.

The token creation result should include copyable MCP configuration examples that show the bearer token configured for HTTP MCP clients.

## Docker and Environment

Compose adds a PostgreSQL container:

- service name: `postgres`
- database credentials supplied through `.env`
- persistent volume for PostgreSQL data

The board service uses `DATABASE_URL` and depends on `postgres`. Existing VibePod network behavior stays intact, including the `vibepod-board` network alias.

Update `.env.example` with:

- `DATABASE_URL`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

Remove `DATA_DIR` from normal runtime docs once PostgreSQL storage is in place.

## Temporary JSON Import

Provide an explicit temporary script:

```bash
npm run import:json -- ./data/board.json
```

The script connects to `DATABASE_URL`, initializes the schema if needed, reads the existing JSON format, applies the same normalization/migration rules currently used for legacy JSON data, and inserts projects, ideas, board cards, documents, and activity while preserving IDs and timestamps.

The script fails if target board tables already contain data. A deliberate override flag can be added for development use, but the default must avoid accidental duplication.

The running server does not automatically import JSON on startup.

## Error Handling

Authentication failures return `401`.

Valid authentication with insufficient project access returns `403`.

Missing or invalid project IDs continue to return the existing validation/not-found style errors, unless access should be hidden for project tokens. For project tokens, a forbidden project should return `403` rather than leaking full project data.

Startup errors for missing production admin credentials or an invalid database connection should be explicit and logged before process exit.

## Testing Strategy

Add tests for:

- PostgreSQL-backed store parity with current project, idea, board card, document, activity, and GitHub metadata behavior.
- JSON import preserving IDs, timestamps, project ownership, task numbers, statuses, links, and activity.
- Admin login, logout, and session-protected REST access.
- Token creation, token hashing, raw-token one-time response, project mapping, revocation, and last-used updates.
- REST project scoping for single-project and multi-project tokens.
- MCP bearer-token requirement and project scoping for tools and resource reads.
- Docker Compose includes PostgreSQL, `DATABASE_URL`, the shared VibePod network alias, and no runtime `/data` board volume.

Existing client utility tests should remain unchanged unless the login/token-management UI introduces new focused utilities.

## Out of Scope

This design does not add user accounts beyond the single env-configured admin identity.

This design does not add per-token permission levels such as read-only versus write. Project tokens can perform the supported project-scoped API/MCP operations.

This design does not keep JSON file storage as a selectable runtime backend.
