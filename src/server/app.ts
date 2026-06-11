import cors from "cors";
import express, {
  type ErrorRequestHandler,
  type Request,
  type RequestHandler
} from "express";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";

import { boardColumns, documentKinds, ideaStatuses } from "../shared/types.js";
import {
  parseBearerToken,
  sessionFromRequest,
  setSessionCookie,
  type AdminSessionManager
} from "./auth.js";
import { createMcpRouter } from "./mcp.js";
import { adminAccess, tokenAccess, type AccessContext, type BoardDataStore } from "./store.js";

type CreateAppOptions = {
  store: BoardDataStore;
  sessions: AdminSessionManager;
  publicDir?: string;
};

type GitHubIssue = {
  html_url: string;
  number: number;
};

const loginSchema = z.object({
  username: z.string(),
  password: z.string()
});

const ideaSchema = z.object({
  projectId: z.string().optional(),
  title: z.string().trim().min(1),
  summary: z.string().optional().default(""),
  details: z.string().optional().default(""),
  labels: z.array(z.string()).optional().default([]),
  acceptanceCriteria: z.array(z.string()).optional().default([])
});

const ideaStatusSchema = z.preprocess(
  (value) => (value === "dennied" ? "denied" : value),
  z.enum(ideaStatuses)
);

const updateIdeaSchema = z.object({
  title: z.string().trim().min(1).optional(),
  summary: z.string().optional(),
  details: z.string().optional(),
  labels: z.array(z.string()).optional(),
  acceptanceCriteria: z.array(z.string()).optional(),
  status: ideaStatusSchema.optional()
});

const readySchema = z.object({
  available: z.boolean().optional().default(true)
});

const updateBoardCardSchema = z.object({
  column: z.enum(boardColumns).optional(),
  branchName: z.string().optional()
});

const projectKeySchema = z.string().trim().regex(/^[A-Z]{1,3}$/, "Project ID must be 1 to 3 capital letters");

const projectSchema = z.object({
  key: projectKeySchema,
  title: z.string().trim().min(1),
  summary: z.string().optional().default("")
});

const updateProjectSchema = z.object({
  key: projectKeySchema.optional(),
  title: z.string().trim().min(1).optional(),
  summary: z.string().optional()
});

const documentSchema = z.object({
  projectId: z.string().optional(),
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

const tokenSchema = z.object({
  name: z.string().trim().min(1),
  projectIds: z.array(z.string().trim().min(1)).min(1)
});

const updateTokenSchema = z.object({
  name: z.string().trim().min(1).optional(),
  projectIds: z.array(z.string().trim().min(1)).min(1).optional()
});

export const createApp = ({ store, sessions, publicDir }: CreateAppOptions) => {
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

  app.post("/api/auth/login", (req, res) => {
    const { username, password } = loginSchema.parse(req.body);
    const session = sessions.login(username, password);
    if (!session) {
      res.status(401).json({ error: "Invalid username or password" });
      return;
    }
    setSessionCookie(res, session.cookie);
    res.json({ authenticated: true, username: session.username });
  });

  app.post("/api/auth/logout", (req, res) => {
    const session = sessionFromRequest(req, sessions);
    if (session) {
      sessions.logout(session.id);
    }
    setSessionCookie(res, sessions.clearCookie);
    res.json({ authenticated: false });
  });

  app.get("/api/auth/me", (req, res) => {
    const session = sessionFromRequest(req, sessions);
    if (!session) {
      res.json({ authenticated: false });
      return;
    }
    res.json({ authenticated: true, username: session.username });
  });

  app.get("/api/mcp-info", (_req, res) => {
    res.json({
      endpoint: "/mcp",
      transport: "streamable-http",
      auth: "bearer",
      tools: [
        "list_projects",
        "create_project",
        "list_ideas",
        "create_idea",
        "mark_idea_ready",
        "list_board",
        "move_board_card",
        "update_board_card",
        "create_document",
        "list_documents"
      ],
      resources: ["vibepod-board://state"]
    });
  });

  app.get(
    "/api/projects",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      res.json({ items: await store.listProjects(accessFromResponse(req)) });
    })
  );

  app.post(
    "/api/projects",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.createProject(projectSchema.parse(req.body));
      res.status(201).json({ item });
    })
  );

  app.patch(
    "/api/projects/:id",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.updateProject(routeParam(req.params.id), updateProjectSchema.parse(req.body));
      res.json({ item });
    })
  );

  app.get(
    "/api/ideas",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      res.json({ items: await store.listIdeas(accessFromResponse(req), queryParam(req.query.projectId)) });
    })
  );

  app.post(
    "/api/ideas",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.createIdea(accessFromResponse(req), ideaSchema.parse(req.body));
      res.status(201).json({ item });
    })
  );

  app.patch(
    "/api/ideas/:id",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.updateIdea(
        accessFromResponse(req),
        routeParam(req.params.id),
        updateIdeaSchema.parse(req.body)
      );
      res.json({ item });
    })
  );

  app.post(
    "/api/ideas/:id/ready",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const { available } = readySchema.parse(req.body ?? {});
      const item = await store.setIdeaBoardAvailability(
        accessFromResponse(req),
        routeParam(req.params.id),
        available
      );
      res.json({ item });
    })
  );

  app.post(
    "/api/ideas/:id/sync-github",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const access = accessFromResponse(req);
      const id = routeParam(req.params.id);
      const github = await maybeCreateGitHubIssue(store, access, id);
      const card = await store.createBoardCardFromIdea(access, id, github);
      res.json({ mode: github.githubMode, card });
    })
  );

  app.get(
    "/api/board",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      res.json({ columns: await store.getBoardColumns(accessFromResponse(req), queryParam(req.query.projectId)) });
    })
  );

  app.patch(
    "/api/board/:id",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const input = updateBoardCardSchema.parse(req.body);
      const item = await store.updateBoardCard(accessFromResponse(req), routeParam(req.params.id), input);
      res.json({ item });
    })
  );

  app.get(
    "/api/documents",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      res.json({ items: await store.listDocuments(accessFromResponse(req), queryParam(req.query.projectId)) });
    })
  );

  app.post(
    "/api/documents",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.createDocument(accessFromResponse(req), documentSchema.parse(req.body));
      res.status(201).json({ item });
    })
  );

  app.patch(
    "/api/documents/:id",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.updateDocument(
        accessFromResponse(req),
        routeParam(req.params.id),
        updateDocumentSchema.parse(req.body)
      );
      res.json({ item });
    })
  );

  app.get(
    "/api/activity",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      res.json({ items: await store.listActivity(accessFromResponse(req)) });
    })
  );

  app.get(
    "/api/tokens",
    requireAdmin(store, sessions),
    asyncHandler(async (_req, res) => {
      res.json({ items: await store.listApiTokens() });
    })
  );

  app.post(
    "/api/tokens",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const created = await store.createApiToken(tokenSchema.parse(req.body));
      res.status(201).json(created);
    })
  );

  app.patch(
    "/api/tokens/:id",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.updateApiToken(routeParam(req.params.id), updateTokenSchema.parse(req.body));
      res.json({ item });
    })
  );

  app.post(
    "/api/tokens/:id/revoke",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.revokeApiToken(routeParam(req.params.id));
      res.json({ item });
    })
  );

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

const requireAccess = (store: BoardDataStore, sessions: AdminSessionManager): RequestHandler =>
  asyncHandler(async (req, res, next) => {
    const access = await getAccess(req, store, sessions);
    if (!access) {
      res.status(401).json({ error: "Authentication required" });
      return;
    }
    res.locals.access = access;
    next();
  });

const requireAdmin = (store: BoardDataStore, sessions: AdminSessionManager): RequestHandler =>
  asyncHandler(async (req, res, next) => {
    const access = await getAccess(req, store, sessions);
    if (!access) {
      res.status(401).json({ error: "Authentication required" });
      return;
    }
    if (access.kind !== "admin") {
      res.status(403).json({ error: "Admin access required" });
      return;
    }
    res.locals.access = access;
    next();
  });

const getAccess = async (
  req: Request,
  store: BoardDataStore,
  sessions: AdminSessionManager
): Promise<AccessContext | null> => {
  const session = sessionFromRequest(req, sessions);
  if (session) {
    return adminAccess(session.username);
  }

  const token = parseBearerToken(req.headers.authorization);
  if (!token) {
    return null;
  }
  const authenticated = await store.authenticateApiToken(token);
  return authenticated ? tokenAccess(authenticated.tokenId, authenticated.projectIds) : null;
};

const accessFromResponse = (req: Request): AccessContext => req.res?.locals.access as AccessContext;

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
  if (message.includes("not allowed") || message.includes("not mapped") || message.includes("Admin access")) {
    res.status(403).json({ error: message });
    return;
  }
  if (message.includes("Only ready ideas")) {
    res.status(409).json({ error: message });
    return;
  }
  if (message.includes("already used")) {
    res.status(409).json({ error: message });
    return;
  }
  res.status(500).json({ error: message });
};

const routeParam = (value: string | string[]): string => (Array.isArray(value) ? value[0] : value);

const queryParam = (value: unknown): string | undefined => {
  if (Array.isArray(value)) {
    return queryParam(value[0]);
  }
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
};

const maybeCreateGitHubIssue = async (store: BoardDataStore, access: AccessContext, ideaId: string) => {
  const token = process.env.GITHUB_TOKEN;
  const repository = process.env.GITHUB_REPOSITORY;
  if (!token || !repository) {
    return { githubMode: "local" as const };
  }

  const idea = (await store.getState(access)).ideas.find((item) => item.id === ideaId);
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
