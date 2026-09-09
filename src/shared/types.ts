export const ideaStatuses = ["idea", "refining", "ready", "denied"] as const;
export const boardColumns = [
  "ready",
  "planned",
  "in_progress",
  "review",
  "done",
] as const;
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
  /** Tasks that must be finished before this one can start. */
  dependsOn: string[];
  /** Tasks that wait for this one; derived from the dependency graph. */
  blocks: string[];
  /** Subset of dependsOn that is not done or denied yet; derived. */
  blockedBy: string[];
  githubIssueUrl?: string;
  githubIssueNumber?: number;
  repositoryLocalPath?: string;
  repositoryRemoteUrl?: string;
  /**
   * Free text naming whoever holds the task, by convention an agent naming
   * itself, such as `Claude::Subagent101::Worktree12`. No format is enforced.
   */
  assignee?: string;
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
  /** Task ids the linked task depends on; derived from the dependency graph. */
  dependsOn: string[];
  /** Subset of dependsOn that is not done or denied yet; derived. */
  blockedBy: string[];
  /** Mirrors the linked task's assignee; see `Idea.assignee`. */
  assignee?: string;
  readinessScore?: number;
  readinessReason?: string;
  readinessEvaluatedAt?: string;
  createdAt: string;
  updatedAt: string;
};

export type ReadinessEvent = {
  id: string;
  ideaId: string;
  score: number;
  reason: string;
  createdAt: string;
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
  readinessEvents: ReadinessEvent[];
  documents: PlanDocument[];
  activity: ActivityEvent[];
};

export type ProjectBundle = {
  bundleVersion: 1;
  exportedAt: string;
  project: Project;
  ideas: Idea[];
  boardCards: BoardCard[];
  readinessEvents: ReadinessEvent[];
  documents: PlanDocument[];
};

export type ImportProjectOptions = {
  replaceExisting: boolean;
};

export type ImportProjectResult = {
  item: Project;
  replaced: boolean;
};

export type CreateProjectInput = {
  key: string;
  title: string;
  summary?: string;
};

export type UpdateProjectInput = Partial<
  Pick<Project, "key" | "title" | "summary">
>;

export type CreateIdeaInput = {
  projectId?: string;
  title: string;
  summary?: string;
  details?: string;
  labels?: string[];
  acceptanceCriteria?: string[];
  dependsOn?: string[];
  repositoryLocalPath?: string;
  repositoryRemoteUrl?: string;
  assignee?: string;
};

export type UpdateIdeaInput = Partial<
  Pick<
    Idea,
    | "title"
    | "summary"
    | "details"
    | "labels"
    | "acceptanceCriteria"
    | "dependsOn"
    | "status"
    | "repositoryLocalPath"
    | "repositoryRemoteUrl"
    | "assignee"
  >
> &
  ConcurrencyGuard & {
    /** True puts the task on the board, false removes its card. */
    onBoard?: boolean;
    /** Records a readiness score in the same write. */
    readiness?: SetCardReadinessInput;
  };

export type MarkIdeaReadyOptions = {
  readiness?: SetCardReadinessInput;
};

export type CreateBoardCardOptions = {
  githubMode: "local" | "github";
  githubIssueUrl?: string;
  githubIssueNumber?: number;
};

export type UpdateBoardCardInput = Partial<
  Pick<
    BoardCard,
    | "column"
    | "branchName"
    | "details"
    | "repositoryLocalPath"
    | "repositoryRemoteUrl"
    | "assignee"
  >
> &
  ConcurrencyGuard;

export type SetCardReadinessInput = {
  score: number;
  reason: string;
};

export type BatchIdeaUpdate = { id: string } & UpdateIdeaInput;
export type BatchBoardCardUpdate = { id: string } & UpdateBoardCardInput;

/** A batch is applied in one transaction; more than this is refused outright. */
export const batchLimit = 50;

/**
 * `assignee` and `unassigned` widen each other rather than narrowing: asked
 * together they select tasks held by one of the named holders *or* held by
 * nobody, because "mine or free" is the question an agent picking up work asks.
 */
export type AssigneeFilter = {
  assignee?: string[];
  unassigned?: boolean;
};

export type IdeaListFilter = AssigneeFilter & {
  projectId?: string;
  status?: IdeaStatus[];
  updatedSince?: string;
  limit?: number;
  cursor?: string;
};

export type BoardListFilter = AssigneeFilter & {
  projectId?: string;
  column?: BoardColumn[];
  updatedSince?: string;
  limit?: number;
  cursor?: string;
};

export type ReadinessListFilter = {
  projectId?: string;
  tasks?: string[];
  latestOnly?: boolean;
};

export type DocumentListFilter = {
  projectId?: string;
  kind?: DocumentKind[];
  updatedSince?: string;
};

/**
 * Optional guard on a write: when set, the write is refused unless the record
 * still carries this `updatedAt`.
 */
export type ConcurrencyGuard = {
  expectedUpdatedAt?: string;
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
  Pick<
    PlanDocument,
    "title" | "kind" | "content" | "linkedIdeaIds" | "linkedCardIds"
  >
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
