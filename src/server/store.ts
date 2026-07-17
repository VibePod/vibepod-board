import type {
  ApiTokenSummary,
  BoardCard,
  BoardColumn,
  BoardColumns,
  BoardData,
  CreateApiTokenInput,
  CreateBoardCardOptions,
  CreateDocumentInput,
  CreateIdeaInput,
  CreateProjectInput,
  Idea,
  PlanDocument,
  Project,
  SetCardReadinessInput,
  UpdateApiTokenInput,
  UpdateBoardCardInput,
  UpdateDocumentInput,
  UpdateIdeaInput,
  UpdateProjectInput
} from "../shared/types.js";

export type AdminAccess = {
  kind: "admin";
  username: string;
};

export type TokenAccess = {
  kind: "token";
  tokenId: string;
  projectIds: string[];
};

export type AccessContext = AdminAccess | TokenAccess;

export type AuthenticatedToken = {
  tokenId: string;
  projectIds: string[];
};

export type CreatedApiToken = {
  item: ApiTokenSummary;
  token: string;
};

export interface BoardDataStore {
  getState(access: AccessContext): Promise<BoardData>;
  listProjects(access: AccessContext): Promise<Project[]>;
  createProject(input: CreateProjectInput): Promise<Project>;
  updateProject(id: string, input: UpdateProjectInput): Promise<Project>;
  listIdeas(access: AccessContext, projectId?: string): Promise<Idea[]>;
  createIdea(access: AccessContext, input: CreateIdeaInput): Promise<Idea>;
  updateIdea(access: AccessContext, id: string, input: UpdateIdeaInput): Promise<Idea>;
  markIdeaReady(access: AccessContext, id: string): Promise<Idea>;
  setIdeaBoardAvailability(access: AccessContext, id: string, available: boolean): Promise<Idea>;
  createBoardCardFromIdea(
    access: AccessContext,
    id: string,
    options: CreateBoardCardOptions
  ): Promise<BoardCard>;
  listBoardCards(access: AccessContext, projectId?: string): Promise<BoardCard[]>;
  getBoardColumns(access: AccessContext, projectId?: string): Promise<BoardColumns>;
  updateBoardCard(access: AccessContext, id: string, input: UpdateBoardCardInput): Promise<BoardCard>;
  moveBoardCard(access: AccessContext, id: string, column: BoardColumn): Promise<BoardCard>;
  setCardReadiness(access: AccessContext, id: string, input: SetCardReadinessInput): Promise<BoardCard>;
  setIdeaReadiness(access: AccessContext, id: string, input: SetCardReadinessInput): Promise<Idea>;
  listDocuments(access: AccessContext, projectId?: string): Promise<PlanDocument[]>;
  createDocument(access: AccessContext, input: CreateDocumentInput): Promise<PlanDocument>;
  updateDocument(access: AccessContext, id: string, input: UpdateDocumentInput): Promise<PlanDocument>;
  listActivity(access: AccessContext): Promise<BoardData["activity"]>;
  listApiTokens(): Promise<ApiTokenSummary[]>;
  createApiToken(input: CreateApiTokenInput): Promise<CreatedApiToken>;
  updateApiToken(id: string, input: UpdateApiTokenInput): Promise<ApiTokenSummary>;
  revokeApiToken(id: string): Promise<ApiTokenSummary>;
  authenticateApiToken(token: string): Promise<AuthenticatedToken | null>;
}

export const adminAccess = (username: string): AdminAccess => ({ kind: "admin", username });

export const tokenAccess = (tokenId: string, projectIds: string[]): TokenAccess => ({
  kind: "token",
  tokenId,
  projectIds
});

export const isAdminAccess = (access: AccessContext): access is AdminAccess => access.kind === "admin";

export const assertCanAccessProject = (access: AccessContext, projectId: string) => {
  if (access.kind === "admin" || access.projectIds.includes(projectId)) {
    return;
  }
  throw new Error(`Token is not allowed to access project: ${projectId}`);
};

export const filterProjectIds = (access: AccessContext, projectIds: string[]): string[] =>
  access.kind === "admin"
    ? projectIds
    : projectIds.filter((projectId) => access.projectIds.includes(projectId));

export const defaultProjectIdForCreate = (
  access: AccessContext,
  requestedProjectId: string | undefined
): string | undefined => {
  if (access.kind === "admin") {
    return requestedProjectId;
  }
  if (requestedProjectId) {
    assertCanAccessProject(access, requestedProjectId);
    return requestedProjectId;
  }
  const [firstProjectId] = access.projectIds;
  if (!firstProjectId) {
    throw new Error("Token is not mapped to any projects");
  }
  return firstProjectId;
};
