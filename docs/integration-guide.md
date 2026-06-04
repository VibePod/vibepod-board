# Integration Guide

`vibepod-board` exposes the same planning state through the browser UI, REST API, and MCP. When it runs in Docker Compose, it joins the shared VibePod Docker network so running agent containers can reach the MCP endpoint by service alias.

## Network

The default Docker network is `vibepod-network`, matching the VibePod CLI default.

```bash
docker network create vibepod-network
```

If your VibePod install uses a different network name, set `VIBEPOD_NETWORK` before starting the board:

```bash
VIBEPOD_NETWORK=my-vibepod-network docker compose up --build
```

The Compose service joins the network with the alias `vibepod-board`.

## Start the Board

```bash
cd vibepod-board
docker compose up --build
```

Open the browser UI from the Docker host at:

```text
http://localhost:3000
```

Open it from another machine on the same network at:

```text
http://<docker-host-ip>:3000
```

## MCP URLs

Use the Docker-network URL from another VibePod container:

```text
http://vibepod-board:3000/mcp
```

Use the host URL from a desktop MCP client running outside Docker:

```text
http://localhost:3000/mcp
```

The MCP endpoint uses Streamable HTTP. REST metadata is available at:

```text
http://localhost:3000/api/mcp-info
```

## Client Examples

Use the Docker-network URL when the agent is running in a VibePod container:

```text
http://vibepod-board:3000/mcp
```

Use `http://localhost:3000/mcp` instead when the agent runs directly on the Docker host.

### Claude Code

```bash
claude mcp add --transport http --scope user vibepod-board http://vibepod-board:3000/mcp
claude mcp list
```

Project `.mcp.json` alternative:

```json
{
  "mcpServers": {
    "vibepod-board": {
      "type": "http",
      "url": "http://vibepod-board:3000/mcp"
    }
  }
}
```

Open Claude Code and run `/mcp` to confirm the server is connected.

### Codex

```bash
codex mcp add vibepod-board --url http://vibepod-board:3000/mcp
codex mcp list
```

`~/.codex/config.toml` alternative:

```toml
[mcp_servers.vibepod-board]
url = "http://vibepod-board:3000/mcp"
```

Use `/mcp` in the Codex TUI to inspect the active server.

### Auggie

```bash
auggie mcp add vibepod-board --transport http --url http://vibepod-board:3000/mcp
auggie mcp list
```

`~/.augment/settings.json` alternative:

```json
{
  "mcpServers": {
    "vibepod-board": {
      "type": "http",
      "url": "http://vibepod-board:3000/mcp"
    }
  }
}
```

Open Auggie and use `/mcp` to confirm the server is available.

### OpenCode

Add this to `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "vibepod-board": {
      "type": "remote",
      "url": "http://vibepod-board:3000/mcp",
      "enabled": true
    }
  }
}
```

Run `opencode mcp list`, then prompt OpenCode to use `vibepod-board`.

## Connect a Running VibePod Container

If an agent container is already running but is not on the VibePod network, connect it:

```bash
docker network connect vibepod-network <container-name-or-id>
```

Then configure the agent-side MCP client to use:

```text
http://vibepod-board:3000/mcp
```

If you start an agent through VibePod, pass the network explicitly when needed:

```bash
vp run codex --network vibepod-network
```

## Smoke Test from Docker

This checks that another container on the same network can resolve and reach the board:

```bash
docker run --rm --network vibepod-network node:22-alpine \
  node -e "fetch('http://vibepod-board:3000/api/health').then(r => r.text()).then(console.log)"
```

Expected response:

```json
{"ok":true,"service":"vibepod-board","mcp":"/mcp"}
```

## Optional GitHub Sync

Ready tasks become board cards locally. Without GitHub settings, the optional API sync endpoint updates the local board card without creating a GitHub issue.

To create real GitHub issues:

```bash
GITHUB_TOKEN=ghp_xxx
GITHUB_REPOSITORY=owner/repo
docker compose up --build
```

Ready tasks appear on the board first. The optional sync endpoint can then attach GitHub issue metadata.
