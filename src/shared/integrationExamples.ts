export type IntegrationExample = {
  id: string;
  name: string;
  description: string;
  docsUrl: string;
  configLabel: string;
  config: string;
  command?: string;
  verify: string;
};

export const dockerMcpUrl = "http://vibepod-board:3000/mcp";
export const hostMcpUrl = "http://localhost:3000/mcp";

export const endpointOptions = [
  {
    label: "From VibePod containers",
    url: dockerMcpUrl,
    description:
      "Use this when Claude Code, Codex, Auggie, or OpenCode runs inside the VibePod Docker network.",
  },
  {
    label: "From the Docker host",
    url: hostMcpUrl,
    description:
      "Use this when the MCP client runs directly on the host that publishes port 3000.",
  },
] as const;

export const bearerTokenExample = "<vibepod-board-token>";

export const authorizationHeader = (token = bearerTokenExample) =>
  `Bearer ${token}`;

export const integrationExamplesForToken = (
  token = bearerTokenExample,
): IntegrationExample[] => [
  {
    id: "claude-code",
    name: "Claude Code",
    description:
      "Add vibepod-board as a remote HTTP MCP server with a bearer token.",
    docsUrl: "https://docs.anthropic.com/en/docs/claude-code/mcp",
    command: `claude mcp add-json vibepod-board '{"type":"http","url":"http://vibepod-board:3000/mcp","headers":{"Authorization":"${authorizationHeader(token)}"}}'`,
    configLabel: "Project .mcp.json alternative",
    config: `{
  "mcpServers": {
    "vibepod-board": {
      "type": "http",
      "url": "http://vibepod-board:3000/mcp",
      "headers": {
        "Authorization": "${authorizationHeader(token)}"
      }
    }
  }
}`,
    verify: "Run claude mcp list, then open Claude Code and use /mcp.",
  },
  {
    id: "codex",
    name: "Codex",
    description: "Use the Codex MCP config with an Authorization header.",
    docsUrl: "https://developers.openai.com/codex/mcp",
    configLabel: "~/.codex/config.toml",
    config: `[mcp_servers.vibepod-board]
url = "http://vibepod-board:3000/mcp"
http_headers = { Authorization = "${authorizationHeader(token)}" }`,
    verify: "Run codex mcp list, then use /mcp in the Codex TUI.",
  },
  {
    id: "auggie",
    name: "Auggie",
    description:
      "Persist the board endpoint with a bearer token in Augment settings.",
    docsUrl: "https://docs.augmentcode.com/cli/integrations",
    command: `auggie mcp add vibepod-board --transport http --url http://vibepod-board:3000/mcp --header "Authorization: ${authorizationHeader(token)}"`,
    configLabel: "~/.augment/settings.json",
    config: `{
  "mcpServers": {
    "vibepod-board": {
      "type": "http",
      "url": "http://vibepod-board:3000/mcp",
      "headers": {
        "Authorization": "${authorizationHeader(token)}"
      }
    }
  }
}`,
    verify: "Run auggie mcp list or open Auggie and use /mcp.",
  },
  {
    id: "opencode",
    name: "OpenCode",
    description:
      "Add vibepod-board as a remote MCP server with request headers.",
    docsUrl: "https://opencode.ai/docs/mcp-servers/",
    configLabel: "opencode.json",
    config: `{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "vibepod-board": {
      "type": "remote",
      "url": "http://vibepod-board:3000/mcp",
      "enabled": true,
      "headers": {
        "Authorization": "${authorizationHeader(token)}"
      }
    }
  }
}`,
    verify: "Run opencode mcp list, then prompt OpenCode to use vibepod-board.",
  },
];

export const integrationExamples = integrationExamplesForToken();
