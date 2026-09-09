import {
  compactBoardCard,
  compactDocument,
  compactIdea,
  refBoardCard,
  refIdea,
  type ViewLevel,
} from "../shared/projections.js";
import type { BoardCard, Idea, PlanDocument } from "../shared/types.js";
import type { AccessContext, BoardDataStore } from "./store.js";

/**
 * Every task id a response mentions, including dependency ids that are not
 * themselves in the result set, so keys resolve everywhere they appear.
 */
const referencedIds = (ideas: Idea[], cards: BoardCard[]): string[] => {
  const ids = new Set<string>();
  for (const idea of ideas) {
    ids.add(idea.id);
    for (const id of idea.dependsOn) {
      ids.add(id);
    }
    for (const id of idea.blockedBy) {
      ids.add(id);
    }
  }
  for (const card of cards) {
    if (card.ideaId) {
      ids.add(card.ideaId);
    }
    for (const id of card.blockedBy) {
      ids.add(id);
    }
  }
  return [...ids];
};

/**
 * `key` is attached on the way out, never on the stored record: the project
 * bundle schema is strict and rejects any unknown property on an Idea.
 */
export const projectIdeas = async (
  store: BoardDataStore,
  access: AccessContext,
  ideas: Idea[],
  view: ViewLevel,
  cards: BoardCard[] = [],
): Promise<unknown[]> => {
  const keys = await store.getTaskKeys(access, referencedIds(ideas, cards));
  if (view === "full") {
    return ideas.map((idea) => ({ ...idea, key: keys.get(idea.id) }));
  }
  const columns = new Map(
    cards
      .filter((card): card is BoardCard & { ideaId: string } =>
        Boolean(card.ideaId),
      )
      .map((card) => [card.ideaId, card.column]),
  );
  return ideas.map((idea) =>
    view === "ref"
      ? refIdea(idea, keys)
      : compactIdea(idea, keys, columns.get(idea.id)),
  );
};

export const projectBoardCards = async (
  store: BoardDataStore,
  access: AccessContext,
  cards: BoardCard[],
  view: ViewLevel,
): Promise<unknown[]> => {
  const keys = await store.getTaskKeys(access, referencedIds([], cards));
  if (view === "full") {
    return cards.map((card) => ({
      ...card,
      key: card.ideaId ? keys.get(card.ideaId) : undefined,
    }));
  }
  return cards.map((card) =>
    view === "ref" ? refBoardCard(card, keys) : compactBoardCard(card, keys),
  );
};

export const projectDocuments = (
  documents: PlanDocument[],
  view: ViewLevel,
): unknown[] => (view === "full" ? documents : documents.map(compactDocument));

export const projectIdea = async (
  store: BoardDataStore,
  access: AccessContext,
  idea: Idea,
  view: ViewLevel,
  cards: BoardCard[] = [],
): Promise<unknown> =>
  (await projectIdeas(store, access, [idea], view, cards))[0];

export const projectBoardCard = async (
  store: BoardDataStore,
  access: AccessContext,
  card: BoardCard,
  view: ViewLevel,
): Promise<unknown> =>
  (await projectBoardCards(store, access, [card], view))[0];
