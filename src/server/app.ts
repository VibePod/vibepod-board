import cors from "cors";
import express, { type ErrorRequestHandler, type RequestHandler } from "express";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";

import { boardColumns, documentKinds, ideaStatuses } from "../shared/types.js";
import type { BoardStore } from "./storage.js";
import { createMcpRouter } from "./mcp.js";

type CreateAppOptions = {
  store: BoardStore;
  publicDir?: string;
};

type GitHubIssue = {
  html_url: string;
  number: number;
};

const ideaSchema = z.object({
  title: z.string().trim().min(1),
  summary: z.string().optional().default(""),
  details: z.string().optional().default(""),
  labels: z.array(z.string()).optional().default([]),
  acceptanceCriteria: z.array(z.string()).optional().default([])
});

const updateIdeaSchema = z.object({
  title: z.string().trim().min(1).optional(),
  summary: z.string().optional(),
  details: z.string().optional(),
  labels: z.array(z.string()).optional(),
  acceptanceCriteria: z.array(z.string()).optional(),
  status: z.enum(ideaStatuses).optional()
});

const moveCardSchema = z.object({
  column: z.enum(boardColumns)
});

const documentSchema = z.object({
  title: z.string().trim().min(1),
  kind: z.enum(documentKinds).optional().default("execution_plan"),
  content: z.string().optional().default(""),
  linkedIdeaIds: z.array(z.string()).optional().default([]),
  linkedCardIds: z.array(z.string()).optional().default([])
});

const updateDocumentSchema = z.object({
  title: z.string().trim().min(1).optional(),
  kind: z.enum(documentKinds).optional(),
  content: z.string().optional(),
  linkedIdeaIds: z.array(z.string()).optional(),
  linkedCardIds: z.array(z.string()).optional()
});

export const createApp = ({ store, publicDir }: CreateAppOptions) => {
  const app = express();
  app.disable("x-powered-by");
  app.use(cors());
  app.use(express.json({ limit: "2mb" }));

  app.get("/api/health", (_req, res) => {
    res.json({
      ok: true,
      service: "vibepod-board",
      mcp: "/mcp"
    });
  });

  app.get("/api/mcp-info", (_req, res) => {
    res.json({
      endpoint: "/mcp",
      transport: "streamable-http",
      tools: [
        "list_ideas",
        "create_idea",
        "mark_idea_ready",
        "list_board",
        "move_board_card",
        "create_document",
        "list_documents"
      ],
      resources: ["vibepod-board://state"]
    });
  });

  app.get("/api/ideas", (_req, res) => {
    res.json({ items: store.listIdeas() });
  });

  app.post(
    "/api/ideas",
    asyncHandler((req, res) => {
      const item = store.createIdea(ideaSchema.parse(req.body));
      res.status(201).json({ item });
    })
  );

  app.patch(
    "/api/ideas/:id",
    asyncHandler((req, res) => {
      const item = store.updateIdea(routeParam(req.params.id), updateIdeaSchema.parse(req.body));
      res.json({ item });
    })
  );

  app.post(
    "/api/ideas/:id/ready",
    asyncHandler((req, res) => {
      const item = store.markIdeaReady(routeParam(req.params.id));
      res.json({ item });
    })
  );

  app.post(
    "/api/ideas/:id/sync-github",
    asyncHandler(async (req, res) => {
      const id = routeParam(req.params.id);
      const github = await maybeCreateGitHubIssue(store, id);
      const card = store.createBoardCardFromIdea(id, github);
      res.json({ mode: github.githubMode, card });
    })
  );

  app.get("/api/board", (_req, res) => {
    res.json({ columns: store.getBoardColumns() });
  });

  app.patch(
    "/api/board/:id",
    asyncHandler((req, res) => {
      const { column } = moveCardSchema.parse(req.body);
      const item = store.moveBoardCard(routeParam(req.params.id), column);
      res.json({ item });
    })
  );

  app.get("/api/documents", (_req, res) => {
    res.json({ items: store.listDocuments() });
  });

  app.post(
    "/api/documents",
    asyncHandler((req, res) => {
      const item = store.createDocument(documentSchema.parse(req.body));
      res.status(201).json({ item });
    })
  );

  app.patch(
    "/api/documents/:id",
    asyncHandler((req, res) => {
      const item = store.updateDocument(routeParam(req.params.id), updateDocumentSchema.parse(req.body));
      res.json({ item });
    })
  );

  app.get("/api/activity", (_req, res) => {
    res.json({ items: store.listActivity() });
  });

  app.use("/mcp", createMcpRouter(store));

  if (publicDir && existsSync(publicDir)) {
    app.use(express.static(publicDir));
    app.get(/^\/(?!api|mcp).*/, (_req, res) => {
      res.sendFile(join(publicDir, "index.html"));
    });
  }

  app.use(errorHandler);
  return app;
};

const asyncHandler =
  (handler: RequestHandler): RequestHandler =>
  (req, res, next) => {
    Promise.resolve(handler(req, res, next)).catch(next);
  };

const errorHandler: ErrorRequestHandler = (error, _req, res, _next) => {
  const message = error instanceof Error ? error.message : "Unknown error";
  if (error instanceof z.ZodError) {
    res.status(400).json({ error: "Validation failed", details: error.flatten() });
    return;
  }
  if (message.includes("not found")) {
    res.status(404).json({ error: message });
    return;
  }
  if (message.includes("Only ready ideas")) {
    res.status(409).json({ error: message });
    return;
  }
  res.status(500).json({ error: message });
};

const routeParam = (value: string | string[]): string => (Array.isArray(value) ? value[0] : value);

const maybeCreateGitHubIssue = async (store: BoardStore, ideaId: string) => {
  const token = process.env.GITHUB_TOKEN;
  const repository = process.env.GITHUB_REPOSITORY;
  if (!token || !repository) {
    return { githubMode: "local" as const };
  }

  const idea = store.getState().ideas.find((item) => item.id === ideaId);
  if (!idea) {
    throw new Error(`Idea not found: ${ideaId}`);
  }

  const response = await fetch(`https://api.github.com/repos/${repository}/issues`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "User-Agent": "vibepod-board"
    },
    body: JSON.stringify({
      title: idea.title,
      labels: idea.labels,
      body: formatIssueBody(idea)
    })
  });

  if (!response.ok) {
    const details = await response.text();
    throw new Error(`GitHub issue creation failed: ${response.status} ${details}`);
  }

  const issue = (await response.json()) as GitHubIssue;
  return {
    githubMode: "github" as const,
    githubIssueUrl: issue.html_url,
    githubIssueNumber: issue.number
  };
};

const formatIssueBody = (idea: { summary: string; details: string; acceptanceCriteria: string[] }) => {
  const sections = [
    idea.summary && `## Summary\n${idea.summary}`,
    idea.details && `## Details\n${idea.details}`,
    idea.acceptanceCriteria.length > 0 &&
      `## Acceptance Criteria\n${idea.acceptanceCriteria.map((item) => `- ${item}`).join("\n")}`
  ].filter(Boolean);

  return sections.join("\n\n") || "Created from vibepod-board.";
};
