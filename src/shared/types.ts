export const ideaStatuses = ["idea", "refining", "ready", "synced"] as const;
export const boardColumns = ["ready", "planned", "in_progress", "review", "done"] as const;
export const documentKinds = ["execution_plan", "design", "notes"] as const;

export type IdeaStatus = (typeof ideaStatuses)[number];
export type BoardColumn = (typeof boardColumns)[number];
export type DocumentKind = (typeof documentKinds)[number];

export type Idea = {
  id: string;
  title: string;
  summary: string;
  details: string;
  status: IdeaStatus;
  labels: string[];
  acceptanceCriteria: string[];
  githubIssueUrl?: string;
  githubIssueNumber?: number;
  createdAt: string;
  updatedAt: string;
};

export type BoardCard = {
  id: string;
  title: string;
  details: string;
  column: BoardColumn;
  ideaId?: string;
  githubIssueUrl?: string;
  githubIssueNumber?: number;
  labels: string[];
  createdAt: string;
  updatedAt: string;
};

export type BoardColumns = Record<BoardColumn, BoardCard[]>;

export type PlanDocument = {
  id: string;
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
  schemaVersion: 1;
  ideas: Idea[];
  boardCards: BoardCard[];
  documents: PlanDocument[];
  activity: ActivityEvent[];
};

export type CreateIdeaInput = {
  title: string;
  summary?: string;
  details?: string;
  labels?: string[];
  acceptanceCriteria?: string[];
};

export type UpdateIdeaInput = Partial<
  Pick<Idea, "title" | "summary" | "details" | "labels" | "acceptanceCriteria" | "status">
>;

export type CreateBoardCardOptions = {
  githubMode: "local" | "github";
  githubIssueUrl?: string;
  githubIssueNumber?: number;
};

export type CreateDocumentInput = {
  title: string;
  kind?: DocumentKind;
  content?: string;
  linkedIdeaIds?: string[];
  linkedCardIds?: string[];
};

export type UpdateDocumentInput = Partial<
  Pick<PlanDocument, "title" | "kind" | "content" | "linkedIdeaIds" | "linkedCardIds">
>;
