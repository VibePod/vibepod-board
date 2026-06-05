import type { BoardColumn, CreateDocumentInput, CreateIdeaInput, CreateProjectInput } from "../shared/types.js";
import { isAdminAccess, type AccessContext, type BoardDataStore } from "./store.js";

export const createMcpToolHandlers = (store: BoardDataStore, access: AccessContext) => ({
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
  async mark_idea_ready(input: { id: string }) {
    return { item: await store.markIdeaReady(access, input.id) };
  },
  async list_board(input: { projectId?: string }) {
    return { columns: await store.getBoardColumns(access, input.projectId) };
  },
  async move_board_card(input: { id: string; column: BoardColumn }) {
    return { item: await store.moveBoardCard(access, input.id, input.column) };
  },
  async create_document(input: CreateDocumentInput) {
    return { item: await store.createDocument(access, input) };
  },
  async list_documents(input: { projectId?: string }) {
    return { items: await store.listDocuments(access, input.projectId) };
  },
  async read_state() {
    return await store.getState(access);
  }
});
