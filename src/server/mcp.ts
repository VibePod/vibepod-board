import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { Router } from "express";
import * as z from "zod/v4";

import { boardColumns, documentKinds, ideaStatuses } from "../shared/types.js";
import { parseBearerToken } from "./auth.js";
import { createMcpToolHandlers } from "./mcpTools.js";
import {
  type AccessContext,
  type BoardDataStore,
  tokenAccess,
} from "./store.js";

const jsonContent = (value: unknown) => ({
  content: [
    {
      type: "text" as const,
      text: JSON.stringify(value, null, 2),
    },
  ],
});

export const createMcpServer = (
  store: BoardDataStore,
  access: AccessContext,
) => {
  const server = new McpServer({
    name: "vibepod-board",
    version: "0.1.0",
  });
  const handlers = createMcpToolHandlers(store, access);

  server.registerResource(
    "board-state",
    "vibepod-board://state",
    {
      title: "VibePod Board State",
      description: "Full scoped vibepod-board state as JSON.",
      mimeType: "application/json",
    },
    async (uri) => ({
      contents: [
        {
          uri: uri.href,
          mimeType: "application/json",
          text: JSON.stringify(await handlers.read_state(), null, 2),
        },
      ],
    }),
  );

  server.registerTool(
    "list_projects",
    {
      title: "List Projects",
      description: "List projects that contain tasks, board cards, and notes.",
    },
    async () => jsonContent(await handlers.list_projects()),
  );

  server.registerTool(
    "create_project",
    {
      title: "Create Project",
      description:
        "Create a project container for tasks, board cards, and notes.",
      inputSchema: {
        key: z.string().regex(/^[A-Z]{1,3}$/),
        title: z.string().min(1),
        summary: z.string().optional(),
      },
    },
    async (input) => jsonContent(await handlers.create_project(input)),
  );

  server.registerTool(
    "list_ideas",
    {
      title: "List Ideas",
      description: "List ideas with refinement and readiness state.",
      inputSchema: {
        projectId: z.string().optional(),
      },
    },
    async ({ projectId }) =>
      jsonContent(await handlers.list_ideas({ projectId })),
  );

  server.registerTool(
    "create_idea",
    {
      title: "Create Idea",
      description: "Create a new idea for refinement.",
      inputSchema: {
        projectId: z.string().optional(),
        title: z.string().min(1),
        summary: z.string().optional(),
        details: z.string().optional(),
        labels: z.array(z.string()).optional(),
        acceptanceCriteria: z.array(z.string()).optional(),
        repositoryLocalPath: z.string().optional(),
        repositoryRemoteUrl: z.string().optional(),
      },
    },
    async (input) => jsonContent(await handlers.create_idea(input)),
  );

  server.registerTool(
    "update_idea",
    {
      title: "Update Idea",
      description:
        "Edit an existing idea's fields. Only provided fields change; omitted fields are left as-is.",
      inputSchema: {
        id: z.string().min(1),
        title: z.string().min(1).optional(),
        summary: z.string().optional(),
        details: z.string().optional(),
        labels: z.array(z.string()).optional(),
        acceptanceCriteria: z.array(z.string()).optional(),
        repositoryLocalPath: z.string().optional(),
        repositoryRemoteUrl: z.string().optional(),
        status: z.enum(ideaStatuses).optional(),
      },
    },
    async (input) => jsonContent(await handlers.update_idea(input)),
  );

  server.registerTool(
    "mark_idea_ready",
    {
      title: "Mark Idea Ready",
      description: "Mark an idea as ready and available on the Kanban board.",
      inputSchema: {
        id: z.string().min(1),
      },
    },
    async ({ id }) => jsonContent(await handlers.mark_idea_ready({ id })),
  );

  server.registerTool(
    "list_board",
    {
      title: "List Board",
      description: "Read the Kanban board columns.",
      inputSchema: {
        projectId: z.string().optional(),
      },
    },
    async ({ projectId }) =>
      jsonContent(await handlers.list_board({ projectId })),
  );

  server.registerTool(
    "move_board_card",
    {
      title: "Move Board Card",
      description: "Move a board card to another Kanban column.",
      inputSchema: {
        id: z.string().min(1),
        column: z.enum(boardColumns),
      },
    },
    async ({ id, column }) =>
      jsonContent(await handlers.move_board_card({ id, column })),
  );

  server.registerTool(
    "update_board_card",
    {
      title: "Update Board Card",
      description:
        "Edit board-card metadata such as implementation branch and repository. Only provided fields change.",
      inputSchema: {
        id: z.string().min(1),
        column: z.enum(boardColumns).optional(),
        branchName: z.string().optional(),
        details: z.string().optional(),
        repositoryLocalPath: z.string().optional(),
        repositoryRemoteUrl: z.string().optional(),
      },
    },
    async (input) => jsonContent(await handlers.update_board_card(input)),
  );

  server.registerTool(
    "set_card_readiness",
    {
      title: "Set Card Readiness",
      description:
        "Record an LLM-evaluated readiness score (1-10) with a short reason on a board card. Does not change updated_at, so a later content edit marks the score stale.",
      inputSchema: {
        id: z.string().min(1),
        score: z.number().int().min(1).max(10),
        reason: z.string().min(1),
      },
    },
    async (input) => jsonContent(await handlers.set_card_readiness(input)),
  );

  server.registerTool(
    "set_idea_readiness",
    {
      title: "Set Idea Readiness",
      description:
        "Record an LLM-evaluated readiness score (1-10) with a short reason on an idea; mirrors onto its linked board card. Does not change updated_at, so a later content edit marks the score stale.",
      inputSchema: {
        id: z.string().min(1),
        score: z.number().int().min(1).max(10),
        reason: z.string().min(1),
      },
    },
    async (input) => jsonContent(await handlers.set_idea_readiness(input)),
  );

  server.registerTool(
    "list_idea_readiness",
    {
      title: "List Idea Readiness History",
      description:
        "List an idea's readiness rating history, newest first (score, reason, date).",
      inputSchema: {
        id: z.string().min(1),
      },
    },
    async ({ id }) => jsonContent(await handlers.list_idea_readiness({ id })),
  );

  server.registerTool(
    "create_document",
    {
      title: "Create Document",
      description: "Create an execution plan, design doc, or notes document.",
      inputSchema: {
        projectId: z.string().optional(),
        title: z.string().min(1),
        kind: z.enum(documentKinds).optional(),
        content: z.string().optional(),
        linkedIdeaIds: z.array(z.string()).optional(),
        linkedCardIds: z.array(z.string()).optional(),
      },
    },
    async (input) => jsonContent(await handlers.create_document(input)),
  );

  server.registerTool(
    "update_document",
    {
      title: "Update Document",
      description:
        "Edit an existing document's fields. Only provided fields change; omitted fields are left as-is.",
      inputSchema: {
        id: z.string().min(1),
        title: z.string().min(1).optional(),
        kind: z.enum(documentKinds).optional(),
        content: z.string().optional(),
        linkedIdeaIds: z.array(z.string()).optional(),
        linkedCardIds: z.array(z.string()).optional(),
      },
    },
    async (input) => jsonContent(await handlers.update_document(input)),
  );

  server.registerTool(
    "list_documents",
    {
      title: "List Documents",
      description: "List execution plans and other planning documents.",
      inputSchema: {
        projectId: z.string().optional(),
      },
    },
    async ({ projectId }) =>
      jsonContent(await handlers.list_documents({ projectId })),
  );

  return server;
};

export const createMcpRouter = (store: BoardDataStore) => {
  const router = Router();

  router.post("/", async (req, res) => {
    const token = parseBearerToken(req.headers.authorization);
    if (!token) {
      res.status(401).json({ error: "Authentication required" });
      return;
    }
    const authenticated = await store.authenticateApiToken(token);
    if (!authenticated) {
      res.status(401).json({ error: "Invalid or revoked token" });
      return;
    }

    const server = createMcpServer(
      store,
      tokenAccess(authenticated.tokenId, authenticated.projectIds),
    );
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
    });

    try {
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
    } catch (error) {
      if (!res.headersSent) {
        res.status(500).json({
          jsonrpc: "2.0",
          error: {
            code: -32603,
            message:
              error instanceof Error ? error.message : "Internal server error",
          },
          id: null,
        });
      }
    } finally {
      await transport.close();
      await server.close();
    }
  });

  router.get("/", (_req, res) => {
    res.status(405).json({
      jsonrpc: "2.0",
      error: {
        code: -32000,
        message: "Method not allowed.",
      },
      id: null,
    });
  });

  router.delete("/", (_req, res) => {
    res.status(405).json({
      jsonrpc: "2.0",
      error: {
        code: -32000,
        message: "Method not allowed.",
      },
      id: null,
    });
  });

  return router;
};
