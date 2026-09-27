import { describe, expect, it } from "vitest";

import {
  archivedTaskRows,
  filterAndSortArchivedTasks,
} from "../src/client/archiveUtils.js";
import type { BoardCard, Idea } from "../src/shared/types.js";

const timestamp = "2026-06-04T00:00:00.000Z";

const card = (patch: Partial<BoardCard>): BoardCard => ({
  id: "card-1",
  projectId: "project-1",
  title: "Card title",
  details: "",
  column: "done",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const idea = (patch: Partial<Idea>): Idea => ({
  id: "idea-1",
  projectId: "project-1",
  taskNumber: 1,
  title: "Task title",
  summary: "",
  details: "",
  status: "ready",
  labels: [],
  acceptanceCriteria: [],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  createdAt: timestamp,
  updatedAt: timestamp,
  ...patch,
});

const ideas = [
  idea({ id: "idea-1", taskNumber: 2, title: "Login form", labels: ["ui"] }),
  idea({ id: "idea-2", taskNumber: 10, title: "API schema", labels: ["api"] }),
];

const cards = [
  card({
    id: "card-1",
    ideaId: "idea-1",
    title: "Login form",
    labels: ["ui"],
    branchName: "vp-login",
    archivedAt: "2026-06-05T00:00:00.000Z",
  }),
  card({
    id: "card-2",
    ideaId: "idea-2",
    title: "API schema",
    labels: ["api"],
    archivedAt: "2026-06-07T00:00:00.000Z",
  }),
  card({
    id: "card-3",
    title: "Orphan card",
    labels: ["legacy"],
    archivedAt: "2026-06-06T00:00:00.000Z",
  }),
];

describe("archived task rows", () => {
  it("combines each archived card with its task identity", () => {
    const rows = archivedTaskRows(
      cards,
      new Map(ideas.map((item) => [item.id, item])),
      "APP",
    );

    expect(rows[0]).toEqual({
      cardId: "card-1",
      ideaId: "idea-1",
      taskId: "APP-2",
      taskNumber: 2,
      title: "Login form",
      labels: ["ui"],
      branchName: "vp-login",
      archivedAt: "2026-06-05T00:00:00.000Z",
    });
    expect(rows[2]).toMatchObject({
      cardId: "card-3",
      ideaId: undefined,
      taskId: "",
      title: "Orphan card",
    });
  });

  it("uses the live task title over the card's copy", () => {
    const [row] = archivedTaskRows(
      [cards[0]],
      new Map([["idea-1", idea({ taskNumber: 2, title: "Renamed" })]]),
      "APP",
    );
    expect(row.title).toBe("Renamed");
  });
});

describe("filtering and sorting archived tasks", () => {
  const rows = archivedTaskRows(
    cards,
    new Map(ideas.map((item) => [item.id, item])),
    "APP",
  );
  const ids = (sorted: { cardId: string }[]) => sorted.map((row) => row.cardId);

  it("sorts newest archived first by default", () => {
    expect(
      ids(
        filterAndSortArchivedTasks(rows, { search: "", sort: "archived_desc" }),
      ),
    ).toEqual(["card-2", "card-3", "card-1"]);
    expect(
      ids(
        filterAndSortArchivedTasks(rows, { search: "", sort: "archived_asc" }),
      ),
    ).toEqual(["card-1", "card-3", "card-2"]);
  });

  it("sorts by task number and by title", () => {
    expect(
      ids(filterAndSortArchivedTasks(rows, { search: "", sort: "task_asc" })),
    ).toEqual(["card-1", "card-2", "card-3"]);
    expect(
      ids(filterAndSortArchivedTasks(rows, { search: "", sort: "title_asc" })),
    ).toEqual(["card-2", "card-1", "card-3"]);
  });

  it("searches task ID, title, labels and branch case-insensitively", () => {
    const search = (term: string) =>
      ids(filterAndSortArchivedTasks(rows, { search: term, sort: "task_asc" }));
    expect(search("app-10")).toEqual(["card-2"]);
    expect(search("LOGIN")).toEqual(["card-1"]);
    expect(search("legacy")).toEqual(["card-3"]);
    expect(search("vp-log")).toEqual(["card-1"]);
    expect(search("  ")).toEqual(["card-1", "card-2", "card-3"]);
  });
});
