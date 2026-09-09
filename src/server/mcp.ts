import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { Router } from "express";
import * as z from "zod/v4";

import { viewLevels } from "../shared/projections.js";
import { boardColumns, documentKinds, ideaStatuses } from "../shared/types.js";
import { parseBearerToken } from "./auth.js";
import { createMcpToolHandlers } from "./mcpTools.js";
import {
  type AccessContext,
  type BoardDataStore,
  tokenAccess,
} from "./store.js";

/**
 * Compact on purpose: indentation costs ~38% on a list of small records,
 * because every dependency id lands on its own indented line.
 */
const jsonContent = (value: unknown) => ({
  content: [
    {
      type: "text" as const,
      text: JSON.stringify(value),
    },
  ],
});

export const mcpToolNames = [
  "add_idea_dependency",
  "create_document",
  "create_idea",
  "create_project",
  "get_board_card",
  "get_idea",
  "list_board",
  "list_documents",
  "list_idea_readiness",
  "list_ideas",
  "list_projects",
  "list_readiness",
  "list_work_order",
  "mark_idea_ready",
  "move_board_card",
  "remove_idea_dependency",
  "set_card_readiness",
  "set_idea_dependencies",
  "set_idea_readiness",
  "update_board_card",
  "update_board_cards",
  "update_document",
  "update_idea",
  "update_ideas",
] as const;

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
      annotations: { readOnlyHint: true },
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
      description:
        "List tasks with refinement and readiness state. Accepts a project id, key or title, and filters by status, assignee or modification time.",
      inputSchema: {
        projectId: z.string().optional(),
        status: z.array(z.enum(ideaStatuses)).optional(),
        updatedSince: z
          .string()
          .min(1)
          .optional()
          .describe("ISO timestamp; returns only tasks changed after it."),
        assignee: z
          .array(z.string().min(1))
          .optional()
          .describe("Holders to match exactly, such as Claude::Subagent101."),
        unassigned: z
          .boolean()
          .optional()
          .describe(
            "True also returns tasks nobody holds; with assignee the two widen each other.",
          ),
        view: z
          .enum(viewLevels)
          .optional()
          .describe(
            "compact (default) omits free text and names dependencies by key; full returns the whole record.",
          ),
        limit: z
          .number()
          .int()
          .min(1)
          .max(500)
          .optional()
          .describe("Defaults to 200; pass nextCursor to continue."),
        cursor: z.string().min(1).optional(),
      },
      annotations: { readOnlyHint: true },
    },
    async (input) => jsonContent(await handlers.list_ideas(input)),
  );

  server.registerTool(
    "get_idea",
    {
      title: "Get Task",
      description:
        "Read one task by reference: its id, its key such as VP-236, or a bare task number when the token covers one project.",
      inputSchema: {
        idea: z.string().min(1),
        view: z.enum(viewLevels).optional(),
      },
      annotations: { readOnlyHint: true },
    },
    async (input) => jsonContent(await handlers.get_idea(input)),
  );

  server.registerTool(
    "get_board_card",
    {
      title: "Get Board Card",
      description:
        "Read one board card by reference: its id, or the key of the task it belongs to such as VP-236.",
      inputSchema: {
        card: z.string().min(1),
        view: z.enum(viewLevels).optional(),
      },
      annotations: { readOnlyHint: true },
    },
    async (input) => jsonContent(await handlers.get_board_card(input)),
  );

  server.registerTool(
    "create_idea",
    {
      title: "Create Idea",
      description:
        "Create a task for refinement. The project accepts an id, key or title; omitting it uses the token's project.",
      inputSchema: {
        projectId: z.string().optional(),
        title: z.string().min(1),
        summary: z.string().optional(),
        details: z.string().optional(),
        labels: z.array(z.string()).optional(),
        acceptanceCriteria: z.array(z.string()).optional(),
        dependsOn: z
          .array(z.string())
          .optional()
          .describe(
            "Ids of tasks in the same project that must be done first.",
          ),
        repositoryLocalPath: z.string().optional(),
        repositoryRemoteUrl: z.string().optional(),
        assignee: z
          .string()
          .optional()
          .describe(
            "Free text naming who holds the task, such as Claude::Subagent101::Worktree12. Empty string releases it.",
          ),
        view: z.enum(viewLevels).optional(),
      },
    },
    async (input) => jsonContent(await handlers.create_idea(input)),
  );

  server.registerTool(
    "update_idea",
    {
      title: "Update Idea",
      description:
        "Edit one task. Accepts an id, a key such as VP-236, or a bare task number when the token covers one project. Only provided fields change. Setting status to ready puts the task on the board; onBoard false removes its card. Pass readiness to record a score in the same write, and expectedUpdatedAt to refuse the write if the task changed since you read it. To claim a task safely, write assignee with the expectedUpdatedAt you read, so a competing claim is refused rather than overwritten. Echoes a compact record unless view says otherwise.",
      inputSchema: {
        id: z.string().min(1),
        title: z.string().min(1).optional(),
        summary: z.string().optional(),
        details: z.string().optional(),
        labels: z.array(z.string()).optional(),
        acceptanceCriteria: z.array(z.string()).optional(),
        dependsOn: z
          .array(z.string())
          .optional()
          .describe("Replaces the full set of blocking task ids when given."),
        repositoryLocalPath: z.string().optional(),
        repositoryRemoteUrl: z.string().optional(),
        assignee: z
          .string()
          .optional()
          .describe(
            "Free text naming who holds the task, such as Claude::Subagent101::Worktree12. Empty string releases it.",
          ),
        status: z.enum(ideaStatuses).optional(),
        onBoard: z
          .boolean()
          .optional()
          .describe(
            "True puts the task on the Kanban board, false removes its card.",
          ),
        readiness: z
          .object({
            score: z.number().int().min(1).max(10),
            reason: z.string().min(1),
          })
          .optional()
          .describe("Records a readiness score in the same write."),
        view: z.enum(viewLevels).optional(),
        expectedUpdatedAt: z
          .string()
          .min(1)
          .optional()
          .describe(
            "Refuse the write if the record changed since this updatedAt.",
          ),
      },
      annotations: { idempotentHint: true },
    },
    async (input) => jsonContent(await handlers.update_idea(input)),
  );

  server.registerTool(
    "update_ideas",
    {
      title: "Update Tasks",
      description:
        "Apply up to 50 task writes in one transaction. Each item is an update_idea input including its own optional expectedUpdatedAt. All-or-nothing: one failing item rolls the whole batch back and names its position. Returns bare references unless view says otherwise.",
      inputSchema: {
        items: z.array(z.record(z.string(), z.unknown())),
        view: z.enum(viewLevels).optional(),
      },
    },
    async (input) =>
      jsonContent(
        await handlers.update_ideas(
          input as unknown as { items: never[]; view?: never },
        ),
      ),
  );

  server.registerTool(
    "update_board_cards",
    {
      title: "Update Board Cards",
      description:
        "Apply up to 50 card writes in one transaction, moving and editing in the same call. All-or-nothing, same as update_ideas.",
      inputSchema: {
        items: z.array(z.record(z.string(), z.unknown())),
        view: z.enum(viewLevels).optional(),
      },
    },
    async (input) =>
      jsonContent(
        await handlers.update_board_cards(
          input as unknown as { items: never[]; view?: never },
        ),
      ),
  );

  server.registerTool(
    "add_idea_dependency",
    {
      title: "Add Task Dependency",
      description:
        "Make a task depend on another task in the same project. The dependency must be finished (board column done) or denied before the task is unblocked. Cycles are rejected.",
      inputSchema: {
        id: z.string().min(1).describe("Task that is blocked."),
        dependsOnId: z.string().min(1).describe("Task that must finish first."),
        view: z.enum(viewLevels).optional(),
      },
    },
    async (input) => jsonContent(await handlers.add_idea_dependency(input)),
  );

  server.registerTool(
    "remove_idea_dependency",
    {
      title: "Remove Task Dependency",
      description: "Drop one dependency edge between two tasks.",
      inputSchema: {
        id: z.string().min(1),
        dependsOnId: z.string().min(1),
        view: z.enum(viewLevels).optional(),
      },
    },
    async (input) => jsonContent(await handlers.remove_idea_dependency(input)),
  );

  server.registerTool(
    "set_idea_dependencies",
    {
      title: "Set Task Dependencies",
      description:
        "Replace the full set of tasks a task depends on. Pass an empty list to clear all dependencies.",
      inputSchema: {
        id: z.string().min(1),
        dependsOnIds: z.array(z.string()),
        view: z.enum(viewLevels).optional(),
      },
    },
    async (input) => jsonContent(await handlers.set_idea_dependencies(input)),
  );

  server.registerTool(
    "list_work_order",
    {
      title: "List Work Order",
      description:
        "List tasks in dependency-resolved execution order. Each item reports position, wave (0 = no dependencies), blockedBy, isBlocked, isComplete, and isActionable. Work items with isActionable true can be started now; anything in cyclicTaskIds could not be ordered.",
      inputSchema: {
        projectId: z.string().optional(),
      },
      annotations: { readOnlyHint: true },
    },
    async (input) => jsonContent(await handlers.list_work_order(input)),
  );

  server.registerTool(
    "mark_idea_ready",
    {
      title: "Mark Idea Ready",
      description:
        "Mark a task ready and put it on the Kanban board. Accepts an id or a key such as VP-236, and an optional readiness score recorded in the same write.",
      inputSchema: {
        id: z.string().min(1),
        readiness: z
          .object({
            score: z.number().int().min(1).max(10),
            reason: z.string().min(1),
          })
          .optional(),
        view: z.enum(viewLevels).optional(),
      },
      annotations: { destructiveHint: true },
    },
    async (input) => jsonContent(await handlers.mark_idea_ready(input)),
  );

  server.registerTool(
    "list_board",
    {
      title: "List Board",
      description:
        "Read the Kanban board columns. Accepts a project id, key or title, and filters by column, assignee or modification time.",
      inputSchema: {
        projectId: z.string().optional(),
        column: z.array(z.enum(boardColumns)).optional(),
        updatedSince: z.string().min(1).optional(),
        assignee: z
          .array(z.string().min(1))
          .optional()
          .describe("Holders to match exactly, such as Claude::Subagent101."),
        unassigned: z
          .boolean()
          .optional()
          .describe(
            "True also returns tasks nobody holds; with assignee the two widen each other.",
          ),
        view: z.enum(viewLevels).optional(),
      },
      annotations: { readOnlyHint: true },
    },
    async (input) => jsonContent(await handlers.list_board(input)),
  );

  server.registerTool(
    "move_board_card",
    {
      title: "Move Board Card",
      description:
        "Deprecated: use update_board_card, which moves and edits in one call. Move a board card to another Kanban column.",
      inputSchema: {
        id: z.string().min(1),
        column: z.enum(boardColumns),
        view: z.enum(viewLevels).optional(),
      },
      annotations: { idempotentHint: true },
    },
    async (input) => jsonContent(await handlers.move_board_card(input)),
  );

  server.registerTool(
    "update_board_card",
    {
      title: "Update Board Card",
      description:
        "Edit one board card, moving and editing in the same call. Accepts a card id or the key of its task such as VP-236. Only provided fields change. Pass expectedUpdatedAt to refuse a stale write. Echoes a compact record unless view says otherwise.",
      inputSchema: {
        id: z.string().min(1),
        column: z.enum(boardColumns).optional(),
        branchName: z.string().optional(),
        details: z.string().optional(),
        repositoryLocalPath: z.string().optional(),
        repositoryRemoteUrl: z.string().optional(),
        assignee: z
          .string()
          .optional()
          .describe(
            "Free text naming who holds the task, such as Claude::Subagent101::Worktree12. Empty string releases it.",
          ),
        view: z.enum(viewLevels).optional(),
        expectedUpdatedAt: z
          .string()
          .min(1)
          .optional()
          .describe(
            "Refuse the write if the record changed since this updatedAt.",
          ),
      },
      annotations: { idempotentHint: true },
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
        view: z.enum(viewLevels).optional(),
      },
      annotations: { idempotentHint: true },
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
        view: z.enum(viewLevels).optional(),
      },
      annotations: { idempotentHint: true },
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
      annotations: { readOnlyHint: true },
    },
    async (input) => jsonContent(await handlers.list_idea_readiness(input)),
  );

  server.registerTool(
    "list_readiness",
    {
      title: "List Readiness",
      description:
        "Latest readiness score per task for a whole project, or for named tasks. Accepts project keys and task keys such as VP-236. Set latestOnly to false for the full history.",
      inputSchema: {
        project: z.string().min(1).optional(),
        tasks: z.array(z.string().min(1)).optional(),
        latestOnly: z.boolean().optional(),
      },
      annotations: { readOnlyHint: true },
    },
    async (input) => jsonContent(await handlers.list_readiness(input)),
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
      description:
        "List execution plans and other planning documents. Accepts a project id, key or title, and filters by kind or by modification time.",
      inputSchema: {
        projectId: z.string().optional(),
        kind: z.array(z.enum(documentKinds)).optional(),
        updatedSince: z.string().min(1).optional(),
        view: z.enum(viewLevels).optional(),
      },
      annotations: { readOnlyHint: true },
    },
    async (input) => jsonContent(await handlers.list_documents(input)),
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
