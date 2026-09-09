import { existsSync } from "node:fs";
import { join } from "node:path";
import cors from "cors";
import express, {
  type ErrorRequestHandler,
  type Request,
  type RequestHandler,
} from "express";
import { z } from "zod";
import { projectBundleSchema } from "../shared/projectBundle.js";
import { type ViewLevel, viewLevels } from "../shared/projections.js";
import {
  type BoardColumn,
  batchLimit,
  boardColumns,
  type DocumentKind,
  documentKinds,
  type IdeaStatus,
  ideaStatuses,
} from "../shared/types.js";
import {
  type AdminSessionManager,
  parseBearerToken,
  sessionFromRequest,
  setSessionCookie,
} from "./auth.js";
import { createMcpRouter, mcpToolNames } from "./mcp.js";
import {
  type AccessContext,
  adminAccess,
  type BoardDataStore,
  tokenAccess,
} from "./store.js";
import { projectBoardCards, projectIdeas } from "./views.js";

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
  password: z.string(),
});

const ideaSchema = z.object({
  projectId: z.string().optional(),
  title: z.string().trim().min(1),
  summary: z.string().optional().default(""),
  details: z.string().optional().default(""),
  labels: z.array(z.string()).optional().default([]),
  acceptanceCriteria: z.array(z.string()).optional().default([]),
  dependsOn: z.array(z.string()).optional().default([]),
  repositoryLocalPath: z.string().optional(),
  repositoryRemoteUrl: z.string().optional(),
  assignee: z.string().optional(),
});

const ideaStatusSchema = z.preprocess(
  (value) => (value === "dennied" ? "denied" : value),
  z.enum(ideaStatuses),
);

const readinessSchema = z.object({
  score: z.number().int().min(1).max(10),
  reason: z.string().trim().min(1),
});

const updateIdeaSchema = z.object({
  title: z.string().trim().min(1).optional(),
  summary: z.string().optional(),
  details: z.string().optional(),
  labels: z.array(z.string()).optional(),
  acceptanceCriteria: z.array(z.string()).optional(),
  dependsOn: z.array(z.string()).optional(),
  repositoryLocalPath: z.string().optional(),
  repositoryRemoteUrl: z.string().optional(),
  assignee: z.string().optional(),
  status: ideaStatusSchema.optional(),
  onBoard: z.boolean().optional(),
  readiness: readinessSchema.optional(),
  expectedUpdatedAt: z.string().min(1).optional(),
});

const dependencySchema = z.object({
  dependsOnId: z.string().trim().min(1),
});

const dependencyListSchema = z.object({
  dependsOnIds: z.array(z.string().trim().min(1)),
});

const readySchema = z.object({
  available: z.boolean().optional().default(true),
  readiness: readinessSchema.optional(),
});

const updateBoardCardSchema = z.object({
  column: z.enum(boardColumns).optional(),
  branchName: z.string().optional(),
  details: z.string().optional(),
  repositoryLocalPath: z.string().optional(),
  repositoryRemoteUrl: z.string().optional(),
  assignee: z.string().optional(),
  expectedUpdatedAt: z.string().min(1).optional(),
});

const batchIdeaSchema = z.object({
  items: z
    .array(updateIdeaSchema.extend({ id: z.string().trim().min(1) }))
    .max(batchLimit),
});

const batchBoardCardSchema = z.object({
  items: z
    .array(updateBoardCardSchema.extend({ id: z.string().trim().min(1) }))
    .max(batchLimit),
});

const projectKeySchema = z
  .string()
  .trim()
  .regex(/^[A-Z]{1,3}$/, "Project ID must be 1 to 3 capital letters");

const projectSchema = z.object({
  key: projectKeySchema,
  title: z.string().trim().min(1),
  summary: z.string().optional().default(""),
});

const updateProjectSchema = z.object({
  key: projectKeySchema.optional(),
  title: z.string().trim().min(1).optional(),
  summary: z.string().optional(),
});

const importProjectRequestSchema = z
  .object({
    bundle: projectBundleSchema,
    replaceExisting: z.boolean().optional().default(false),
  })
  .strict();

const documentSchema = z.object({
  projectId: z.string().optional(),
  title: z.string().trim().min(1),
  kind: z.enum(documentKinds).optional().default("execution_plan"),
  content: z.string().optional().default(""),
  linkedIdeaIds: z.array(z.string()).optional().default([]),
  linkedCardIds: z.array(z.string()).optional().default([]),
});

const updateDocumentSchema = z.object({
  title: z.string().trim().min(1).optional(),
  kind: z.enum(documentKinds).optional(),
  content: z.string().optional(),
  linkedIdeaIds: z.array(z.string()).optional(),
  linkedCardIds: z.array(z.string()).optional(),
});

const tokenSchema = z.object({
  name: z.string().trim().min(1),
  projectIds: z.array(z.string().trim().min(1)).min(1),
});

const updateTokenSchema = z.object({
  name: z.string().trim().min(1).optional(),
  projectIds: z.array(z.string().trim().min(1)).min(1).optional(),
});

export const createApp = ({ store, sessions, publicDir }: CreateAppOptions) => {
  const app = express();
  app.disable("x-powered-by");
  app.use(cors());
  app.use("/api/projects/import", express.json({ limit: "10mb" }));
  app.use(express.json({ limit: "2mb" }));

  app.get("/api/health", (_req, res) => {
    res.json({
      ok: true,
      service: "vibepod-board",
      mcp: "/mcp",
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
      tools: [...mcpToolNames],
      resources: ["vibepod-board://state"],
    });
  });

  app.get(
    "/api/projects",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      res.json({ items: await store.listProjects(accessFromResponse(req)) });
    }),
  );

  app.post(
    "/api/projects/import",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const { bundle, replaceExisting } = importProjectRequestSchema.parse(
        req.body,
      );
      const result = await store.importProject(bundle, { replaceExisting });
      res.status(result.replaced ? 200 : 201).json(result);
    }),
  );

  app.post(
    "/api/projects",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.createProject(projectSchema.parse(req.body));
      res.status(201).json({ item });
    }),
  );

  app.patch(
    "/api/projects/:id",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.updateProject(
        routeParam(req.params.id),
        updateProjectSchema.parse(req.body),
      );
      res.json({ item });
    }),
  );

  app.get(
    "/api/projects/:id/export",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const bundle = await store.exportProject(routeParam(req.params.id));
      res
        .attachment(`${bundle.project.key}-project.json`)
        .type("application/json")
        .send(JSON.stringify(bundle, null, 2));
    }),
  );

  app.get(
    "/api/ideas",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const access = accessFromResponse(req);
      const page = await store.listIdeasPage(access, {
        projectId: queryParam(req.query.projectId),
        status: listParam(req.query.status) as IdeaStatus[] | undefined,
        updatedSince: queryParam(req.query.updatedSince),
        assignee: listParam(req.query.assignee),
        unassigned: booleanParam(req.query.unassigned),
        limit: numberParam(req.query.limit),
        cursor: queryParam(req.query.cursor),
      });
      res.json({
        items: await projectIdeas(
          store,
          access,
          page.items,
          viewParam(req.query.view),
        ),
        ...(page.nextCursor ? { nextCursor: page.nextCursor } : {}),
      });
    }),
  );

  app.post(
    "/api/ideas",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.createIdea(
        accessFromResponse(req),
        ideaSchema.parse(req.body),
      );
      res.status(201).json({ item });
    }),
  );

  app.get(
    "/api/ideas/:id",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.getIdea(
        accessFromResponse(req),
        routeParam(req.params.id),
      );
      res.json({ item });
    }),
  );

  app.get(
    "/api/board/:id",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.getBoardCard(
        accessFromResponse(req),
        routeParam(req.params.id),
      );
      res.json({ item });
    }),
  );

  app.post(
    "/api/ideas/batch",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const { items } = batchIdeaSchema.parse(req.body);
      const written = await store.updateIdeas(accessFromResponse(req), items);
      res.json({
        items: await projectIdeas(
          store,
          accessFromResponse(req),
          written,
          viewParam(req.query.view),
        ),
      });
    }),
  );

  app.patch(
    "/api/ideas/:id",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.updateIdea(
        accessFromResponse(req),
        routeParam(req.params.id),
        updateIdeaSchema.parse(req.body),
      );
      res.json({ item });
    }),
  );

  app.post(
    "/api/ideas/:id/ready",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const { available, readiness } = readySchema.parse(req.body ?? {});
      const item = await store.setIdeaBoardAvailability(
        accessFromResponse(req),
        routeParam(req.params.id),
        available,
        { readiness },
      );
      res.json({ item });
    }),
  );

  app.put(
    "/api/ideas/:id/dependencies",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const { dependsOnIds } = dependencyListSchema.parse(req.body);
      const item = await store.setIdeaDependencies(
        accessFromResponse(req),
        routeParam(req.params.id),
        dependsOnIds,
      );
      res.json({ item });
    }),
  );

  app.post(
    "/api/ideas/:id/dependencies",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const { dependsOnId } = dependencySchema.parse(req.body);
      const item = await store.addIdeaDependency(
        accessFromResponse(req),
        routeParam(req.params.id),
        dependsOnId,
      );
      res.status(201).json({ item });
    }),
  );

  app.delete(
    "/api/ideas/:id/dependencies/:dependsOnId",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.removeIdeaDependency(
        accessFromResponse(req),
        routeParam(req.params.id),
        routeParam(req.params.dependsOnId),
      );
      res.json({ item });
    }),
  );

  app.get(
    "/api/work-order",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const workOrder = await store.getWorkOrder(
        accessFromResponse(req),
        queryParam(req.query.projectId),
      );
      res.json(workOrder);
    }),
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
    }),
  );

  app.get(
    "/api/board",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      res.json({
        columns: await store.getBoardColumns(accessFromResponse(req), {
          projectId: queryParam(req.query.projectId),
          column: listParam(req.query.column) as BoardColumn[] | undefined,
          updatedSince: queryParam(req.query.updatedSince),
          assignee: listParam(req.query.assignee),
          unassigned: booleanParam(req.query.unassigned),
        }),
      });
    }),
  );

  app.post(
    "/api/board/batch",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const { items } = batchBoardCardSchema.parse(req.body);
      const written = await store.updateBoardCards(
        accessFromResponse(req),
        items,
      );
      res.json({
        items: await projectBoardCards(
          store,
          accessFromResponse(req),
          written,
          viewParam(req.query.view),
        ),
      });
    }),
  );

  app.patch(
    "/api/board/:id",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const input = updateBoardCardSchema.parse(req.body);
      const item = await store.updateBoardCard(
        accessFromResponse(req),
        routeParam(req.params.id),
        input,
      );
      res.json({ item });
    }),
  );

  app.post(
    "/api/board/:id/readiness",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const input = readinessSchema.parse(req.body);
      const item = await store.setCardReadiness(
        accessFromResponse(req),
        routeParam(req.params.id),
        input,
      );
      res.json({ item });
    }),
  );

  app.post(
    "/api/ideas/:id/readiness",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const input = readinessSchema.parse(req.body);
      const item = await store.setIdeaReadiness(
        accessFromResponse(req),
        routeParam(req.params.id),
        input,
      );
      res.json({ item });
    }),
  );

  app.get(
    "/api/ideas/:id/readiness",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const items = await store.listIdeaReadiness(
        accessFromResponse(req),
        routeParam(req.params.id),
      );
      res.json({ items });
    }),
  );

  app.get(
    "/api/readiness",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const tasks = queryParam(req.query.tasks);
      const items = await store.listReadiness(accessFromResponse(req), {
        projectId: queryParam(req.query.projectId),
        tasks: tasks
          ? tasks.split(",").map((entry) => entry.trim())
          : undefined,
        latestOnly: queryParam(req.query.latestOnly) !== "false",
      });
      res.json({ items });
    }),
  );

  app.get(
    "/api/documents",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      res.json({
        items: await store.listDocuments(accessFromResponse(req), {
          projectId: queryParam(req.query.projectId),
          kind: listParam(req.query.kind) as DocumentKind[] | undefined,
          updatedSince: queryParam(req.query.updatedSince),
        }),
      });
    }),
  );

  app.post(
    "/api/documents",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.createDocument(
        accessFromResponse(req),
        documentSchema.parse(req.body),
      );
      res.status(201).json({ item });
    }),
  );

  app.patch(
    "/api/documents/:id",
    requireAccess(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.updateDocument(
        accessFromResponse(req),
        routeParam(req.params.id),
        updateDocumentSchema.parse(req.body),
      );
      res.json({ item });
    }),
  );

  app.get(
    "/api/activity",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      res.json({ items: await store.listActivity(accessFromResponse(req)) });
    }),
  );

  app.get(
    "/api/tokens",
    requireAdmin(store, sessions),
    asyncHandler(async (_req, res) => {
      res.json({ items: await store.listApiTokens() });
    }),
  );

  app.post(
    "/api/tokens",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const created = await store.createApiToken(tokenSchema.parse(req.body));
      res.status(201).json(created);
    }),
  );

  app.patch(
    "/api/tokens/:id",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.updateApiToken(
        routeParam(req.params.id),
        updateTokenSchema.parse(req.body),
      );
      res.json({ item });
    }),
  );

  app.post(
    "/api/tokens/:id/revoke",
    requireAdmin(store, sessions),
    asyncHandler(async (req, res) => {
      const item = await store.revokeApiToken(routeParam(req.params.id));
      res.json({ item });
    }),
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

const requireAccess = (
  store: BoardDataStore,
  sessions: AdminSessionManager,
): RequestHandler =>
  asyncHandler(async (req, res, next) => {
    const access = await getAccess(req, store, sessions);
    if (!access) {
      res.status(401).json({ error: "Authentication required" });
      return;
    }
    res.locals.access = access;
    next();
  });

const requireAdmin = (
  store: BoardDataStore,
  sessions: AdminSessionManager,
): RequestHandler =>
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
  sessions: AdminSessionManager,
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
  return authenticated
    ? tokenAccess(authenticated.tokenId, authenticated.projectIds)
    : null;
};

const accessFromResponse = (req: Request): AccessContext =>
  req.res?.locals.access as AccessContext;

const errorHandler: ErrorRequestHandler = (error, req, res, _next) => {
  const message = error instanceof Error ? error.message : "Unknown error";
  if (
    typeof error === "object" &&
    error !== null &&
    "type" in error &&
    error.type === "entity.too.large"
  ) {
    // The import route carries its own larger limit, so name the right one.
    res.status(413).json({
      error: req.path.startsWith("/api/projects/import")
        ? "Project import file must be 10 MiB or smaller"
        : "Request body must be 2 MiB or smaller",
    });
    return;
  }
  if (error instanceof z.ZodError) {
    res
      .status(400)
      .json({ error: "Validation failed", details: error.flatten() });
    return;
  }
  if (message.includes("changed since")) {
    res.status(409).json({ error: message });
    return;
  }
  if (message.startsWith("Ambiguous")) {
    res.status(400).json({ error: message });
    return;
  }
  if (message.includes("not found")) {
    res.status(404).json({ error: message });
    return;
  }
  if (
    message.includes("not allowed") ||
    message.includes("not mapped") ||
    message.includes("Admin access")
  ) {
    res.status(403).json({ error: message });
    return;
  }
  if (message.includes("Only ready ideas")) {
    res.status(409).json({ error: message });
    return;
  }
  if (message.includes("Dependency cycle")) {
    res.status(409).json({ error: message });
    return;
  }
  if (
    message.includes("cannot depend on itself") ||
    message.includes("same project")
  ) {
    res.status(400).json({ error: message });
    return;
  }
  if (message.includes("already used")) {
    res.status(409).json({ error: message });
    return;
  }
  if (message.includes("replacement confirmation is required")) {
    res.status(409).json({ error: message });
    return;
  }
  res.status(500).json({ error: message });
};

const routeParam = (value: string | string[]): string =>
  Array.isArray(value) ? value[0] : value;

const numberParam = (value: unknown): number | undefined => {
  const raw = queryParam(value);
  const parsed = raw ? Number(raw) : Number.NaN;
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
};

const booleanParam = (value: unknown): boolean | undefined =>
  queryParam(value) === "true" ? true : undefined;

const listParam = (value: unknown): string[] | undefined => {
  const raw = queryParam(value);
  if (!raw) {
    return undefined;
  }
  const entries = raw
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  return entries.length > 0 ? entries : undefined;
};

/** REST keeps returning full records unless a caller opts into a smaller view. */
const viewParam = (value: unknown): ViewLevel => {
  const raw = queryParam(value);
  return raw && (viewLevels as readonly string[]).includes(raw)
    ? (raw as ViewLevel)
    : "full";
};

const queryParam = (value: unknown): string | undefined => {
  if (Array.isArray(value)) {
    return queryParam(value[0]);
  }
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
};

const maybeCreateGitHubIssue = async (
  store: BoardDataStore,
  access: AccessContext,
  ideaId: string,
) => {
  const token = process.env.GITHUB_TOKEN;
  const repository = process.env.GITHUB_REPOSITORY;
  if (!token || !repository) {
    return { githubMode: "local" as const };
  }

  const idea = (await store.getState(access)).ideas.find(
    (item) => item.id === ideaId,
  );
  if (!idea) {
    throw new Error(`Idea not found: ${ideaId}`);
  }

  const response = await fetch(
    `https://api.github.com/repos/${repository}/issues`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "User-Agent": "vibepod-board",
      },
      body: JSON.stringify({
        title: idea.title,
        labels: idea.labels,
        body: formatIssueBody(idea),
      }),
    },
  );

  if (!response.ok) {
    const details = await response.text();
    throw new Error(
      `GitHub issue creation failed: ${response.status} ${details}`,
    );
  }

  const issue = (await response.json()) as GitHubIssue;
  return {
    githubMode: "github" as const,
    githubIssueUrl: issue.html_url,
    githubIssueNumber: issue.number,
  };
};

const formatIssueBody = (idea: {
  summary: string;
  details: string;
  acceptanceCriteria: string[];
}) => {
  const sections = [
    idea.summary && `## Summary\n${idea.summary}`,
    idea.details && `## Details\n${idea.details}`,
    idea.acceptanceCriteria.length > 0 &&
      `## Acceptance Criteria\n${idea.acceptanceCriteria.map((item) => `- ${item}`).join("\n")}`,
  ].filter(Boolean);

  return sections.join("\n\n") || "Created from vibepod-board.";
};
