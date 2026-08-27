# Project Data Import and Export Design

## Summary

Add an admin-only browser workflow for exporting one project's portable data as JSON and importing that bundle into another board installation. An import creates the project when its key is new. When the project key already exists, an explicitly confirmed import transactionally replaces the destination project's data while retaining the destination project ID and its API-token assignments.

## Goals

- Export all portable, project-owned data for one project.
- Move projects between board installations without breaking internal task, card, readiness, dependency, or document links.
- Replace an existing destination project by matching its user-facing project key.
- Ensure a failed import never partially changes destination data.
- Make destructive replacement clear and intentional in the browser.

## Non-goals

- Exporting or importing an entire board.
- Copying API tokens, admin sessions, or global activity history.
- Merging individual records into an existing project.
- Automatically remapping entity ID collisions with unrelated projects.
- Adding project import/export to MCP or project-scoped API tokens.

## Bundle Format

The export is a versioned JSON object representing exactly one project:

```ts
type ProjectBundleV1 = {
  bundleVersion: 1;
  exportedAt: string;
  project: Project;
  ideas: Idea[];
  boardCards: BoardCard[];
  readinessEvents: ReadinessEvent[];
  documents: PlanDocument[];
};
```

It contains:

- the project record;
- ideas/tasks, including task numbers, repository metadata, GitHub metadata, readiness state, and dependency IDs;
- board cards and their current columns;
- readiness-history events belonging to the project's ideas; and
- project documents and their task/card links.

The bundle omits global activity events and API tokens because neither is portable project-owned data. The top-level bundle version is independent of the existing whole-board schema version so project bundles can evolve without implying that they are valid whole-board backups.

All child records in the export retain their IDs and timestamps. Preserving these values keeps dependency edges, idea-to-card links, readiness history, and document links exact across installations.

## Server API

### Export

`GET /api/projects/:id/export` requires an admin session. The server verifies the project exists, reads only records owned by that project, constructs the bundle, and returns it as a JSON attachment. The suggested filename is `<KEY>-project.json`.

### Import

`POST /api/projects/import` requires an admin session. Its JSON body contains the bundle and a `replaceExisting` boolean. The endpoint accepts at most 10 MiB and rejects oversized input with a clear error.

The server validates the complete bundle before opening the replacement transaction. Validation includes:

- supported bundle version and exactly one complete project;
- a valid project key and valid enum/status values;
- unique entity IDs and unique positive task numbers;
- every child record belonging to the bundle project;
- dependency endpoints belonging to ideas in the bundle;
- readiness events pointing to ideas in the bundle;
- card-to-idea and document links pointing to records in the bundle; and
- no entity ID collision with records owned by an unrelated destination project.

Unknown or malformed bundle fields are rejected rather than silently accepted. Error responses identify the invalid part without exposing server internals.

## Import Semantics

The project key is the portable identity used to find a destination project.

When no destination project has the imported key, the importer creates a project using the imported project ID and inserts its children unchanged. If that project ID or any child ID is already owned elsewhere, import is rejected.

When a destination project has the imported key:

1. `replaceExisting` must be `true`; otherwise the endpoint returns a conflict without changing data.
2. The importer retains the destination project's internal ID so existing destination API-token assignments remain valid.
3. The destination project title, summary, and timestamps are replaced with values from the bundle.
4. Imported child records are validated against the source project ID and then attached to the retained destination project ID.
5. Existing destination tasks, dependencies, readiness events, board cards, and documents are removed and replaced by imported records.
6. Imported child IDs remain unchanged. A collision with a record outside the destination project rejects the import.

Deletion, insertion, and one local activity event describing the import occur in one database transaction. Any validation, constraint, or insertion failure rolls the entire operation back. Historical activity from the source is not copied.

## Store Boundaries

Project bundle construction and transactional import are store operations so database ownership, collision checks, and transaction behavior stay outside the HTTP and React layers. Shared bundle types and schemas define the transport contract. The existing whole-board JSON migration importer remains separate because it serves a different empty-database migration use case.

The HTTP layer is responsible only for admin authorization, request parsing, download headers, response status mapping, and calling the store operations.

## Browser Experience

The Projects screen gains an **Import Project** button beside **Add Project**. Each project card gains an **Export** action.

Export requests the bundle and downloads the response with a name such as `APP-project.json`.

Import opens a modal containing a JSON file picker. After local parsing, the modal shows the source project's key and title and counts for tasks, board cards, and documents. This preview is informational; the server remains authoritative for validation.

If the key matches an existing project, the modal clearly states that all current tasks, board cards, readiness history, dependencies, and documents in that project will be replaced. The user must explicitly confirm replacement before the client sends `replaceExisting: true`. New-key imports use `replaceExisting: false` and require no destructive warning.

While importing, controls are disabled to prevent duplicate submissions. On success, the app reloads board state, closes the modal, and displays a success message. On failure, the modal remains open and displays the server's actionable error so the user can select a different file or retry.

## Error Handling

- Unauthenticated requests receive the existing authentication response.
- Project-scoped bearer tokens cannot use either endpoint.
- Missing export projects return `404 Not Found`.
- Existing-key imports without explicit replacement confirmation return `409 Conflict`.
- Invalid format, relationships, or unsupported versions return `400 Bad Request`.
- Cross-project ID collisions return `409 Conflict`.
- Oversized request bodies return `413 Payload Too Large`.
- Transaction failures leave the prior project unchanged and return the existing sanitized server-error shape.

## Testing

Store tests cover:

- one-project export scoping;
- round-trip fidelity for tasks, cards, readiness history, dependencies, repository/GitHub metadata, and document links;
- creation when the key is new;
- destination project ID retention when the key exists;
- removal of destination records that are absent from the imported bundle;
- rejection without `replaceExisting`;
- collisions with unrelated project records;
- malformed or cross-project relationships; and
- rollback when insertion fails.

API tests cover admin-only access, attachment headers, not-found behavior, request validation, conflict responses, payload limits, and successful create/replace responses.

Client tests cover export download behavior, import preview counts, destructive confirmation for an existing key, non-destructive new-project import, loading state, success refresh, and visible error handling.

Full verification runs the test suite, server and client typechecks, and the production build.
