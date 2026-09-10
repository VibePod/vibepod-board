import { describe, expect, it } from "vitest";
import {
  assigneeFilterOptions,
  filterColumnsByAssignee,
  matchesAssigneeFilter,
  resolveAssigneeFilter,
  unassignedFilterValue,
} from "../src/client/assigneeFilterUtils.js";
import type { BoardCard, BoardColumns } from "../src/shared/types.js";

const timestamp = "2026-09-10T09:00:00.000Z";
const ada = "Claude::Subagent101::Worktree12";
const grace = "Claude::Subagent202::Worktree7";

const card = (id: string, assignee?: string): BoardCard => ({
  id,
  projectId: "project-1",
  title: id,
  details: "",
  column: "ready",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  assignee,
  createdAt: timestamp,
  updatedAt: timestamp,
});

const columns = (ready: BoardCard[], done: BoardCard[]): BoardColumns => ({
  ready,
  planned: [],
  in_progress: [],
  review: [],
  done,
});

describe("assigneeFilterOptions", () => {
  it("offers every holder present, sorted, plus the two catch-alls", () => {
    const options = assigneeFilterOptions(
      [{ assignee: grace }, { assignee: undefined }],
      [{ assignee: ada }, { assignee: grace }],
    );

    expect(options).toEqual([
      { value: "", label: "All assignees" },
      { value: unassignedFilterValue, label: "Unassigned" },
      { value: ada, label: ada },
      { value: grace, label: grace },
    ]);
  });

  it("omits Unassigned when every task is claimed", () => {
    const options = assigneeFilterOptions([{ assignee: ada }]);

    expect(options.map((option) => option.value)).toEqual(["", ada]);
  });

  it("offers only the catch-all when there is nothing to filter", () => {
    expect(assigneeFilterOptions([], [])).toEqual([
      { value: "", label: "All assignees" },
    ]);
  });
});

describe("matchesAssigneeFilter", () => {
  it("keeps everything when no holder is selected", () => {
    expect(matchesAssigneeFilter(ada, "")).toBe(true);
    expect(matchesAssigneeFilter(undefined, "")).toBe(true);
  });

  it("selects a single holder exactly", () => {
    expect(matchesAssigneeFilter(ada, ada)).toBe(true);
    expect(matchesAssigneeFilter(grace, ada)).toBe(false);
    expect(matchesAssigneeFilter(undefined, ada)).toBe(false);
  });

  it("selects free work under the unassigned sentinel", () => {
    expect(matchesAssigneeFilter(undefined, unassignedFilterValue)).toBe(true);
    expect(matchesAssigneeFilter("", unassignedFilterValue)).toBe(true);
    expect(matchesAssigneeFilter(ada, unassignedFilterValue)).toBe(false);
  });
});

describe("resolveAssigneeFilter", () => {
  it("keeps a filter the options still offer", () => {
    const options = assigneeFilterOptions([{ assignee: ada }]);

    expect(resolveAssigneeFilter(ada, options)).toBe(ada);
  });

  it("falls back to all when the holder released their last task", () => {
    const options = assigneeFilterOptions([{ assignee: grace }]);

    expect(resolveAssigneeFilter(ada, options)).toBe("");
  });
});

describe("filterColumnsByAssignee", () => {
  const board = columns(
    [card("held", ada), card("free")],
    [card("theirs", grace)],
  );

  it("returns every column untouched when nothing is selected", () => {
    const filtered = filterColumnsByAssignee(board, "");

    expect(filtered.ready.map((entry) => entry.id)).toEqual(["held", "free"]);
    expect(filtered.done.map((entry) => entry.id)).toEqual(["theirs"]);
  });

  it("keeps only one holder's cards across every column", () => {
    const filtered = filterColumnsByAssignee(board, ada);

    expect(filtered.ready.map((entry) => entry.id)).toEqual(["held"]);
    expect(filtered.done).toEqual([]);
  });

  it("keeps only unclaimed cards under the sentinel", () => {
    const filtered = filterColumnsByAssignee(board, unassignedFilterValue);

    expect(filtered.ready.map((entry) => entry.id)).toEqual(["free"]);
    expect(filtered.done).toEqual([]);
  });
});
