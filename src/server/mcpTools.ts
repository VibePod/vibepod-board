import type { ViewLevel } from "../shared/projections.js";
import type {
  BatchBoardCardUpdate,
  BatchIdeaUpdate,
  BoardCard,
  BoardColumn,
  BoardListFilter,
  CreateDocumentInput,
  CreateIdeaInput,
  CreateProjectInput,
  DocumentListFilter,
  Idea,
  IdeaListFilter,
  MarkIdeaReadyOptions,
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
import {
  projectBoardCard,
  projectBoardCards,
  projectDocuments,
  projectIdea,
  projectIdeas,
} from "./views.js";

type Viewed = { view?: ViewLevel };

export const createMcpToolHandlers = (
  store: BoardDataStore,
  access: AccessContext,
) => {
  /** Writes echo a compact record: the caller already knows what it sent. */
  const echoIdea = async (idea: Idea, view: ViewLevel = "compact") => ({
    item: await projectIdea(store, access, idea, view),
  });
  const echoCard = async (card: BoardCard, view: ViewLevel = "compact") => ({
    item: await projectBoardCard(store, access, card, view),
  });

  return {
    async list_projects() {
      return { items: await store.listProjects(access) };
    },
    async create_project(input: CreateProjectInput) {
      if (!isAdminAccess(access)) {
        throw new Error("Admin access required");
      }
      return { item: await store.createProject(input) };
    },
    async list_ideas(input: IdeaListFilter & { view?: ViewLevel }) {
      const view = input.view ?? "compact";
      // MCP lists are capped by default; REST stays unlimited.
      const page = await store.listIdeasPage(access, {
        ...input,
        limit: input.limit ?? 200,
      });
      const ideas = page.items;
      const cards =
        view === "compact"
          ? await store.listBoardCards(access, { projectId: input.projectId })
          : [];
      return {
        items: await projectIdeas(store, access, ideas, view, cards),
        ...(page.nextCursor ? { nextCursor: page.nextCursor } : {}),
      };
    },
    async get_idea(input: { idea: string; view?: ViewLevel }) {
      const idea = await store.getIdea(access, input.idea);
      return {
        item: await projectIdea(store, access, idea, input.view ?? "full"),
      };
    },
    async get_board_card(input: { card: string; view?: ViewLevel }) {
      const card = await store.getBoardCard(access, input.card);
      return {
        item: await projectBoardCard(store, access, card, input.view ?? "full"),
      };
    },
    async create_idea(input: CreateIdeaInput & Viewed) {
      const { view, ...changes } = input;
      return echoIdea(await store.createIdea(access, changes), view);
    },
    async update_ideas(input: { items: BatchIdeaUpdate[] } & Viewed) {
      const written = await store.updateIdeas(access, input.items);
      return {
        items: await projectIdeas(store, access, written, input.view ?? "ref"),
      };
    },
    async update_board_cards(
      input: { items: BatchBoardCardUpdate[] } & Viewed,
    ) {
      const written = await store.updateBoardCards(access, input.items);
      return {
        items: await projectBoardCards(
          store,
          access,
          written,
          input.view ?? "ref",
        ),
      };
    },
    async update_idea(input: { id: string } & UpdateIdeaInput & Viewed) {
      const { id, view, ...changes } = input;
      return echoIdea(await store.updateIdea(access, id, changes), view);
    },
    async mark_idea_ready(
      input: { id: string } & MarkIdeaReadyOptions & Viewed,
    ) {
      const { id, view, ...options } = input;
      return echoIdea(await store.markIdeaReady(access, id, options), view);
    },
    async add_idea_dependency(
      input: { id: string; dependsOnId: string } & Viewed,
    ) {
      return echoIdea(
        await store.addIdeaDependency(access, input.id, input.dependsOnId),
        input.view,
      );
    },
    async remove_idea_dependency(
      input: { id: string; dependsOnId: string } & Viewed,
    ) {
      return echoIdea(
        await store.removeIdeaDependency(access, input.id, input.dependsOnId),
        input.view,
      );
    },
    async set_idea_dependencies(
      input: { id: string; dependsOnIds: string[] } & Viewed,
    ) {
      return echoIdea(
        await store.setIdeaDependencies(access, input.id, input.dependsOnIds),
        input.view,
      );
    },
    async list_work_order(input: { projectId?: string }) {
      return await store.getWorkOrder(access, input.projectId);
    },
    async list_board(input: BoardListFilter & { view?: ViewLevel }) {
      const view = input.view ?? "compact";
      const columns = await store.getBoardColumns(access, input);
      const projected: Record<string, unknown[]> = {};
      for (const [column, cards] of Object.entries(columns)) {
        projected[column] = await projectBoardCards(store, access, cards, view);
      }
      return { columns: projected };
    },
    async move_board_card(input: { id: string; column: BoardColumn } & Viewed) {
      return echoCard(
        await store.moveBoardCard(access, input.id, input.column),
        input.view,
      );
    },
    async update_board_card(
      input: { id: string } & UpdateBoardCardInput & Viewed,
    ) {
      const { id, view, ...changes } = input;
      return echoCard(await store.updateBoardCard(access, id, changes), view);
    },
    async set_card_readiness(
      input: { id: string } & SetCardReadinessInput & Viewed,
    ) {
      const { id, view, ...changes } = input;
      return echoCard(await store.setCardReadiness(access, id, changes), view);
    },
    async set_idea_readiness(
      input: { id: string } & SetCardReadinessInput & Viewed,
    ) {
      const { id, view, ...changes } = input;
      return echoIdea(await store.setIdeaReadiness(access, id, changes), view);
    },
    async list_idea_readiness(input: { id: string }) {
      return { items: await store.listIdeaReadiness(access, input.id) };
    },
    async list_readiness(input: {
      project?: string;
      tasks?: string[];
      latestOnly?: boolean;
    }) {
      return {
        items: await store.listReadiness(access, {
          projectId: input.project,
          tasks: input.tasks,
          latestOnly: input.latestOnly,
        }),
      };
    },
    async create_document(input: CreateDocumentInput) {
      return { item: await store.createDocument(access, input) };
    },
    async update_document(input: { id: string } & UpdateDocumentInput) {
      const { id, ...changes } = input;
      return { item: await store.updateDocument(access, id, changes) };
    },
    async list_documents(input: DocumentListFilter & { view?: ViewLevel }) {
      const documents = await store.listDocuments(access, input);
      return { items: projectDocuments(documents, input.view ?? "compact") };
    },
    async read_state() {
      return await store.getState(access);
    },
  };
};
