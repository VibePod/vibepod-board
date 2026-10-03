# CLI follow-up to VP-128: choose distinct default worker names

Repository: `vibepod-cli`

Checkout supplied by the project administrator:
`/root/DEV/VibePod/dev-workspace/public/vibepod-cli`

This is the dedicated follow-up requested by the administrator for VP-128. The
CLI checkout is not available in the board task's container. This description is
ready to add to the project board; publication requires board access.

## Problem

The board now refuses registration of an already connected worker name with a
conflict. Offline registrations can still be replaced, preserving claims, and
REST and MCP accept `replace: true` for an intentional replacement.

The CLI defaults to `<agent>@<host>`. Two workers started with the same agent on
one machine need separate names so they do not share claims or controls.

## Required behavior

- When `--name` is omitted and the default name is connected, try
  `<agent>-2@<host>`, `<agent>-3@<host>`, and so on. Report the selected name.
- If an explicit `--name` is connected, fail with the board's conflict message.
- Preserve the previous name on a restart so the worker can resume its claims.
  Remember the name per profile and repository and use `replace: true` only for
  an intentional restart. A second concurrent process must not treat that saved
  name as permission to replace a running worker.
- Retry only name conflicts; propagate authorization, connection, and other
  failures.

## Acceptance tests

- Two simultaneous workers with the same agent and no `--name` obtain different
  names, including when both initially attempt the same name.
- Occupied numbered names are skipped, and the selected name is reported.
- An occupied explicit name fails with the board's message.
- A restart reuses its old name and retains its claims.
- Saved names are scoped by profile and repository and cannot cause a second
  concurrent process to replace the first worker.
