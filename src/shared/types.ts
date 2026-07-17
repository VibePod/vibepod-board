export const ideaStatuses = ["idea", "refining", "ready", "denied"] as const;
export const boardColumns = ["ready", "planned", "in_progress", "review", "done"] as const;
export const documentKinds = ["execution_plan", "design", "notes"] as const;

export type IdeaStatus = (typeof ideaStatuses)[number];
export type BoardColumn = (typeof boardColumns)[number];
export type DocumentKind = (typeof documentKinds)[number];

export type Project = {
  id: string;
  key: string;
  title: string;
  summary: string;
  createdAt: string;
  updatedAt: string;
};

export type Idea = {
  id: string;
  projectId: string;
  taskNumber: number;
  title: string;
  summary: string;
  details: string;
  status: IdeaStatus;
  labels: string[];
  acceptanceCriteria: string[];
  githubIssueUrl?: string;
  githubIssueNumber?: number;
  repositoryLocalPath?: string;
  repositoryRemoteUrl?: string;
  readinessScore?: number;
  readinessReason?: string;
  readinessEvaluatedAt?: string;
  createdAt: string;
  updatedAt: string;
};

export type BoardCard = {
  id: string;
  projectId: string;
  title: string;
  details: string;
  column: BoardColumn;
  branchName?: string;
  ideaId?: string;
  githubIssueUrl?: string;
  githubIssueNumber?: number;
  repositoryLocalPath?: string;
  repositoryRemoteUrl?: string;
  labels: string[];
  readinessScore?: number;
  readinessReason?: string;
  readinessEvaluatedAt?: string;
  createdAt: string;
  updatedAt: string;
};

export type BoardColumns = Record<BoardColumn, BoardCard[]>;

export type PlanDocument = {
  id: string;
  projectId: string;
  title: string;
  kind: DocumentKind;
  content: string;
  linkedIdeaIds: string[];
  linkedCardIds: string[];
  createdAt: string;
  updatedAt: string;
};

export type ActivityEvent = {
  id: string;
  type: string;
  message: string;
  createdAt: string;
};

export type BoardData = {
  schemaVersion: 3;
  projects: Project[];
  ideas: Idea[];
  boardCards: BoardCard[];
  documents: PlanDocument[];
  activity: ActivityEvent[];
};

export type CreateProjectInput = {
  key: string;
  title: string;
  summary?: string;
};

export type UpdateProjectInput = Partial<Pick<Project, "key" | "title" | "summary">>;

export type CreateIdeaInput = {
  projectId?: string;
  title: string;
  summary?: string;
  details?: string;
  labels?: string[];
  acceptanceCriteria?: string[];
  repositoryLocalPath?: string;
  repositoryRemoteUrl?: string;
};

export type UpdateIdeaInput = Partial<
  Pick<
    Idea,
    | "title"
    | "summary"
    | "details"
    | "labels"
    | "acceptanceCriteria"
    | "status"
    | "repositoryLocalPath"
    | "repositoryRemoteUrl"
  >
>;

export type CreateBoardCardOptions = {
  githubMode: "local" | "github";
  githubIssueUrl?: string;
  githubIssueNumber?: number;
};

export type UpdateBoardCardInput = Partial<
  Pick<BoardCard, "column" | "branchName" | "details" | "repositoryLocalPath" | "repositoryRemoteUrl">
>;

export type SetCardReadinessInput = {
  score: number;
  reason: string;
};

export type CreateDocumentInput = {
  projectId?: string;
  title: string;
  kind?: DocumentKind;
  content?: string;
  linkedIdeaIds?: string[];
  linkedCardIds?: string[];
};

export type UpdateDocumentInput = Partial<
  Pick<PlanDocument, "title" | "kind" | "content" | "linkedIdeaIds" | "linkedCardIds">
>;

export type ApiTokenProject = Pick<Project, "id" | "key" | "title">;

export type ApiTokenSummary = {
  id: string;
  name: string;
  projects: ApiTokenProject[];
  createdAt: string;
  lastUsedAt?: string;
  revokedAt?: string;
};

export type CreateApiTokenInput = {
  name: string;
  projectIds: string[];
};

export type UpdateApiTokenInput = {
  name?: string;
  projectIds?: string[];
};

export type CreatedApiTokenResponse = {
  item: ApiTokenSummary;
  token: string;
};

export type AuthMeResponse = {
  authenticated: boolean;
  username?: string;
};
