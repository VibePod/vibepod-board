import type {
  BoardColumn,
  CreateDocumentInput,
  CreateIdeaInput,
  CreateProjectInput,
  SetCardReadinessInput,
  UpdateBoardCardInput,
  UpdateDocumentInput,
  UpdateIdeaInput,
} from "../shared/types.js";
import {
  type AccessContext,
  type BoardDataStore,
  isAdminAccess,
} from "./store.js";

export const createMcpToolHandlers = (
  store: BoardDataStore,
  access: AccessContext,
) => ({
  async list_projects() {
    return { items: await store.listProjects(access) };
  },
  async create_project(input: CreateProjectInput) {
    if (!isAdminAccess(access)) {
      throw new Error("Admin access required");
    }
    return { item: await store.createProject(input) };
  },
  async list_ideas(input: { projectId?: string }) {
    return { items: await store.listIdeas(access, input.projectId) };
  },
  async create_idea(input: CreateIdeaInput) {
    return { item: await store.createIdea(access, input) };
  },
  async update_idea(input: { id: string } & UpdateIdeaInput) {
    const { id, ...changes } = input;
    return { item: await store.updateIdea(access, id, changes) };
  },
  async mark_idea_ready(input: { id: string }) {
    return { item: await store.markIdeaReady(access, input.id) };
  },
  async list_board(input: { projectId?: string }) {
    return { columns: await store.getBoardColumns(access, input.projectId) };
  },
  async move_board_card(input: { id: string; column: BoardColumn }) {
    return { item: await store.moveBoardCard(access, input.id, input.column) };
  },
  async update_board_card(input: { id: string } & UpdateBoardCardInput) {
    const { id, ...changes } = input;
    return { item: await store.updateBoardCard(access, id, changes) };
  },
  async set_card_readiness(input: { id: string } & SetCardReadinessInput) {
    const { id, ...changes } = input;
    return { item: await store.setCardReadiness(access, id, changes) };
  },
  async set_idea_readiness(input: { id: string } & SetCardReadinessInput) {
    const { id, ...changes } = input;
    return { item: await store.setIdeaReadiness(access, id, changes) };
  },
  async list_idea_readiness(input: { id: string }) {
    return { items: await store.listIdeaReadiness(access, input.id) };
  },
  async create_document(input: CreateDocumentInput) {
    return { item: await store.createDocument(access, input) };
  },
  async update_document(input: { id: string } & UpdateDocumentInput) {
    const { id, ...changes } = input;
    return { item: await store.updateDocument(access, id, changes) };
  },
  async list_documents(input: { projectId?: string }) {
    return { items: await store.listDocuments(access, input.projectId) };
  },
  async read_state() {
    return await store.getState(access);
  },
});
