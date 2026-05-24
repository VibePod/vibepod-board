import { Router } from "express";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import * as z from "zod/v4";

import { boardColumns, documentKinds } from "../shared/types.js";
import type { BoardStore } from "./storage.js";

const jsonContent = (value: unknown) => ({
  content: [
    {
      type: "text" as const,
      text: JSON.stringify(value, null, 2)
    }
  ]
});

export const createMcpServer = (store: BoardStore) => {
  const server = new McpServer({
    name: "vibepod-board",
    version: "0.1.0"
  });

  server.registerResource(
    "board-state",
    "vibepod-board://state",
    {
      title: "VibePod Board State",
      description: "Full vibepod-board state as JSON.",
      mimeType: "application/json"
    },
    async (uri) => ({
      contents: [
        {
          uri: uri.href,
          mimeType: "application/json",
          text: JSON.stringify(store.getState(), null, 2)
        }
      ]
    })
  );

  server.registerTool(
    "list_ideas",
    {
      title: "List Ideas",
      description: "List ideas with refinement and readiness state."
    },
    async () => jsonContent({ items: store.listIdeas() })
  );

  server.registerTool(
    "create_idea",
    {
      title: "Create Idea",
      description: "Create a new idea for refinement.",
      inputSchema: {
        title: z.string().min(1),
        summary: z.string().optional(),
        details: z.string().optional(),
        labels: z.array(z.string()).optional(),
        acceptanceCriteria: z.array(z.string()).optional()
      }
    },
    async (input) => jsonContent({ item: store.createIdea(input) })
  );

  server.registerTool(
    "mark_idea_ready",
    {
      title: "Mark Idea Ready",
      description: "Mark an idea as ready for GitHub sync and board processing.",
      inputSchema: {
        id: z.string().min(1)
      }
    },
    async ({ id }) => jsonContent({ item: store.markIdeaReady(id) })
  );

  server.registerTool(
    "list_board",
    {
      title: "List Board",
      description: "Read the Kanban board columns."
    },
    async () => jsonContent({ columns: store.getBoardColumns() })
  );

  server.registerTool(
    "move_board_card",
    {
      title: "Move Board Card",
      description: "Move a board card to another Kanban column.",
      inputSchema: {
        id: z.string().min(1),
        column: z.enum(boardColumns)
      }
    },
    async ({ id, column }) => jsonContent({ item: store.moveBoardCard(id, column) })
  );

  server.registerTool(
    "create_document",
    {
      title: "Create Document",
      description: "Create an execution plan, design doc, or notes document.",
      inputSchema: {
        title: z.string().min(1),
        kind: z.enum(documentKinds).optional(),
        content: z.string().optional(),
        linkedIdeaIds: z.array(z.string()).optional(),
        linkedCardIds: z.array(z.string()).optional()
      }
    },
    async (input) => jsonContent({ item: store.createDocument(input) })
  );

  server.registerTool(
    "list_documents",
    {
      title: "List Documents",
      description: "List execution plans and other planning documents."
    },
    async () => jsonContent({ items: store.listDocuments() })
  );

  return server;
};

export const createMcpRouter = (store: BoardStore) => {
  const router = Router();

  router.post("/", async (req, res) => {
    const server = createMcpServer(store);
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined
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
            message: error instanceof Error ? error.message : "Internal server error"
          },
          id: null
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
        message: "Method not allowed."
      },
      id: null
    });
  });

  router.delete("/", (_req, res) => {
    res.status(405).json({
      jsonrpc: "2.0",
      error: {
        code: -32000,
        message: "Method not allowed."
      },
      id: null
    });
  });

  return router;
};
