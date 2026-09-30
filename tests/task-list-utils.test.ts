import { afterEach, describe, expect, it, vi } from "vitest";

import { unassignedFilterValue } from "../src/client/assigneeFilterUtils.js";
import {
  filterAndSortTasks,
  filterColumnsBySearch,
  matchesTaskSearch,
} from "../src/client/taskListUtils.js";
import type { BoardCard, BoardColumns, Idea } from "../src/shared/types.js";

const task = (
  patch: Partial<Idea> & Pick<Idea, "id" | "title" | "createdAt">,
): Idea => ({
  projectId: "project-1",
  taskNumber: 1,
  summary: "",
  details: "",
  status: "idea",
  labels: [],
  acceptanceCriteria: [],
  updatedAt: patch.createdAt,
  ...patch,
});

describe("task list utilities", () => {
  it("sorts tasks by latest created first by default", () => {
    const tasks = [
      task({
        id: "old",
        title: "Old task",
        createdAt: "2026-01-01T00:00:00.000Z",
      }),
      task({
        id: "new",
        title: "New task",
        createdAt: "2026-02-01T00:00:00.000Z",
      }),
    ];

    expect(filterAndSortTasks(tasks, {}).map((item) => item.id)).toEqual([
      "new",
      "old",
    ]);
  });

  it("filters by status, label, and search text", () => {
    const tasks = [
      task({
        id: "match",
        title: "Publish launch page",
        summary: "Marketing release",
        status: "ready",
        labels: ["frontend"],
        createdAt: "2026-02-01T00:00:00.000Z",
      }),
      task({
        id: "wrong-label",
        title: "Publish API",
        status: "ready",
        labels: ["backend"],
        createdAt: "2026-03-01T00:00:00.000Z",
      }),
      task({
        id: "wrong-status",
        title: "Publish launch page",
        status: "idea",
        labels: ["frontend"],
        createdAt: "2026-04-01T00:00:00.000Z",
      }),
    ];

    expect(
      filterAndSortTasks(tasks, {
        status: "ready",
        label: "frontend",
        search: "launch",
      }).map((item) => item.id),
    ).toEqual(["match"]);
  });

  it("supports title sorting", () => {
    const tasks = [
      task({ id: "b", title: "Beta", createdAt: "2026-01-01T00:00:00.000Z" }),
      task({ id: "a", title: "Alpha", createdAt: "2026-02-01T00:00:00.000Z" }),
    ];

    expect(
      filterAndSortTasks(tasks, { sort: "title_asc" }).map((item) => item.id),
    ).toEqual(["a", "b"]);
  });

  it("sorts by rating high to low with unrated last", () => {
    const tasks = [
      task({
        id: "unrated",
        title: "Unrated",
        createdAt: "2026-04-01T00:00:00.000Z",
      }),
      task({
        id: "low",
        title: "Low",
        createdAt: "2026-03-01T00:00:00.000Z",
        readinessScore: 3,
      }),
      task({
        id: "high",
        title: "High",
        createdAt: "2026-01-01T00:00:00.000Z",
        readinessScore: 9,
      }),
      task({
        id: "mid-new",
        title: "Mid new",
        createdAt: "2026-05-01T00:00:00.000Z",
        readinessScore: 5,
      }),
      task({
        id: "mid-old",
        title: "Mid old",
        createdAt: "2026-02-01T00:00:00.000Z",
        readinessScore: 5,
      }),
    ];

    expect(
      filterAndSortTasks(tasks, { sort: "rating_desc" }).map((item) => item.id),
    ).toEqual(["high", "mid-new", "mid-old", "low", "unrated"]);
  });

  it("filters by holder and by free work", () => {
    const holder = "Claude::Subagent101::Worktree12";
    const tasks = [
      task({
        id: "held",
        title: "Held task",
        assignee: holder,
        createdAt: "2026-02-01T00:00:00.000Z",
      }),
      task({
        id: "theirs",
        title: "Someone else's task",
        assignee: "Claude::Subagent202::Worktree7",
        createdAt: "2026-03-01T00:00:00.000Z",
      }),
      task({
        id: "free",
        title: "Free task",
        createdAt: "2026-04-01T00:00:00.000Z",
      }),
    ];

    expect(
      filterAndSortTasks(tasks, { assignee: holder }).map((item) => item.id),
    ).toEqual(["held"]);
    expect(
      filterAndSortTasks(tasks, { assignee: unassignedFilterValue }).map(
        (item) => item.id,
      ),
    ).toEqual(["free"]);
    expect(
      filterAndSortTasks(tasks, { assignee: "" }).map((item) => item.id),
    ).toEqual(["free", "theirs", "held"]);
  });
});

describe("task search matcher", () => {
  const target = {
    projectKey: "VP",
    taskNumber: 85,
    title: "Publish launch page",
    summary: "Marketing release",
    details: "Ship before the 2026 conference",
    labels: ["frontend"],
  };

  it("matches the full task ID in any case with surrounding whitespace", () => {
    expect(matchesTaskSearch(target, "VP-85")).toBe(true);
    expect(matchesTaskSearch(target, "vp-85")).toBe(true);
    expect(matchesTaskSearch(target, "  Vp-85 ")).toBe(true);
  });

  it("matches task IDs exactly rather than by prefix", () => {
    expect(matchesTaskSearch({ ...target, taskNumber: 850 }, "VP-85")).toBe(
      false,
    );
    expect(matchesTaskSearch(target, "VP-8")).toBe(false);
    expect(
      matchesTaskSearch(
        { ...target, taskNumber: 850, details: "Follow-up to VP-85" },
        "VP-85",
      ),
    ).toBe(false);
  });

  it("matches bare and hash-prefixed numbers by task number only", () => {
    expect(matchesTaskSearch(target, "85")).toBe(true);
    expect(matchesTaskSearch(target, "#85")).toBe(true);
    expect(matchesTaskSearch(target, " #85 ")).toBe(true);
    expect(
      matchesTaskSearch(
        { ...target, taskNumber: 12, title: "Raise limit to 85 requests" },
        "85",
      ),
    ).toBe(false);
    expect(matchesTaskSearch({ ...target, taskNumber: 850 }, "#85")).toBe(
      false,
    );
    expect(matchesTaskSearch(target, "2026")).toBe(false);
  });

  it("never matches ID or number queries on targets without a task", () => {
    const card = { projectKey: "VP", title: "Card 85", labels: [] };
    expect(matchesTaskSearch(card, "85")).toBe(false);
    expect(matchesTaskSearch(card, "VP-85")).toBe(false);
    expect(matchesTaskSearch(card, "card")).toBe(true);
  });

  it("keeps case-insensitive substring search on text fields", () => {
    expect(matchesTaskSearch(target, "")).toBe(true);
    expect(matchesTaskSearch(target, "   ")).toBe(true);
    expect(matchesTaskSearch(target, "LAUNCH")).toBe(true);
    expect(matchesTaskSearch(target, "marketing")).toBe(true);
    expect(matchesTaskSearch(target, "before the 2026")).toBe(true);
    expect(matchesTaskSearch(target, "frontend")).toBe(true);
    expect(matchesTaskSearch(target, "backend")).toBe(false);
  });

  it("treats ID-shaped text for another project key as plain text", () => {
    const covid = { ...target, details: "Tracks COVID-19 guidance" };
    expect(matchesTaskSearch(covid, "covid-19")).toBe(true);
    expect(matchesTaskSearch(target, "AB-85")).toBe(false);
  });

  describe("under a Turkish default locale", () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });

    it("parses task IDs containing I independently of the locale", () => {
      const toLocaleLowerCase = String.prototype.toLocaleLowerCase;
      // Simulate a tr-TR default locale, where "I" lowercases to dotless "ı".
      vi.spyOn(String.prototype, "toLocaleLowerCase").mockImplementation(
        function (this: string) {
          return toLocaleLowerCase.call(this, "tr");
        },
      );
      const vip = { ...target, projectKey: "VIP", taskNumber: 3 };

      expect(matchesTaskSearch(vip, "VIP-3")).toBe(true);
      expect(matchesTaskSearch(vip, "vip-3")).toBe(true);
      expect(matchesTaskSearch(vip, "Vip-3")).toBe(true);
      expect(matchesTaskSearch(vip, "VIP-4")).toBe(false);
    });
  });

  it("finds tasks by ID in the task list", () => {
    const tasks = [
      task({
        id: "target",
        taskNumber: 85,
        title: "Target",
        createdAt: "2026-01-01T00:00:00.000Z",
      }),
      task({
        id: "longer",
        taskNumber: 850,
        title: "Longer number",
        createdAt: "2026-02-01T00:00:00.000Z",
      }),
      task({
        id: "mentions",
        taskNumber: 3,
        title: "Mentions 85 in text",
        createdAt: "2026-03-01T00:00:00.000Z",
      }),
    ];
    const ids = (search: string) =>
      filterAndSortTasks(tasks, { search, projectKey: "VP" }).map(
        (item) => item.id,
      );

    expect(ids("vp-85")).toEqual(["target"]);
    expect(ids("85")).toEqual(["target"]);
    expect(ids("#85")).toEqual(["target"]);
    expect(ids("mentions")).toEqual(["mentions"]);
  });
});

describe("board search", () => {
  const card = (patch: Partial<BoardCard> & Pick<BoardCard, "id">) =>
    ({
      projectId: "project-1",
      title: "",
      details: "",
      column: "ready",
      labels: [],
      dependsOn: [],
      blockedBy: [],
      ...patch,
    }) as BoardCard;

  it("filters every column by the linked task, or the card itself", () => {
    const ideas = new Map([
      [
        "idea-85",
        task({
          id: "idea-85",
          taskNumber: 85,
          title: "Linked task",
          summary: "Only on the task",
          createdAt: "2026-01-01T00:00:00.000Z",
        }),
      ],
    ]);
    const columns: BoardColumns = {
      ready: [card({ id: "linked", ideaId: "idea-85", title: "Linked task" })],
      planned: [card({ id: "loose", title: "Loose card", labels: ["ops"] })],
      in_progress: [],
      review: [card({ id: "other", title: "Other", details: "Only on 85" })],
      done: [],
    };
    const ids = (search: string) =>
      Object.values(filterColumnsBySearch(columns, search, "VP", ideas))
        .flat()
        .map((item) => item.id);

    expect(ids("")).toEqual(["linked", "loose", "other"]);
    expect(ids("VP-85")).toEqual(["linked"]);
    expect(ids("85")).toEqual(["linked"]);
    expect(ids("only on the task")).toEqual(["linked"]);
    expect(ids("OPS")).toEqual(["loose"]);
    expect(ids("only on")).toEqual(["linked", "other"]);
    expect(filterColumnsBySearch(columns, "85", "VP", ideas).planned).toEqual(
      [],
    );
  });

  it("searches a linked card's own title, details and labels too", () => {
    const ideas = new Map([
      [
        "idea-85",
        task({
          id: "idea-85",
          taskNumber: 85,
          title: "Task title",
          summary: "Task summary",
          details: "Task details",
          labels: ["task-label"],
          createdAt: "2026-01-01T00:00:00.000Z",
        }),
      ],
    ]);
    const columns: BoardColumns = {
      ready: [
        card({
          id: "linked",
          ideaId: "idea-85",
          title: "Card heading",
          details: "Card-only rollout notes",
          labels: ["card-label"],
        }),
      ],
      planned: [],
      in_progress: [],
      review: [],
      pr_ready: [],
      done: [],
    };
    const ids = (search: string) =>
      Object.values(filterColumnsBySearch(columns, search, "VP", ideas))
        .flat()
        .map((item) => item.id);

    expect(ids("card heading")).toEqual(["linked"]);
    expect(ids("rollout notes")).toEqual(["linked"]);
    expect(ids("card-label")).toEqual(["linked"]);
    expect(ids("task title")).toEqual(["linked"]);
    expect(ids("task summary")).toEqual(["linked"]);
    expect(ids("task details")).toEqual(["linked"]);
    expect(ids("task-label")).toEqual(["linked"]);
    expect(ids("VP-85")).toEqual(["linked"]);
    expect(ids("#85")).toEqual(["linked"]);
    expect(ids("unrelated")).toEqual([]);
  });

  it("matches the repository path and remote, which cards no longer show", () => {
    const ideas = new Map([
      [
        "idea-85",
        task({
          id: "idea-85",
          taskNumber: 85,
          title: "Linked task",
          repositoryLocalPath: "/root/DEV/vibepod-board",
          repositoryRemoteUrl: "git@github.com:vibepod/vibepod-board.git",
          createdAt: "2026-01-01T00:00:00.000Z",
        }),
      ],
    ]);
    const columns: BoardColumns = {
      ready: [card({ id: "linked", ideaId: "idea-85", title: "Linked task" })],
      planned: [
        card({
          id: "loose",
          title: "Loose card",
          repositoryLocalPath: "/srv/cli",
          repositoryRemoteUrl: "https://gitlab.com/vibepod/cli.git",
        }),
      ],
      in_progress: [],
      review: [],
      pr_ready: [],
      done: [],
    };
    const ids = (search: string) =>
      Object.values(filterColumnsBySearch(columns, search, "VP", ideas))
        .flat()
        .map((item) => item.id);

    expect(ids("DEV/vibepod-board")).toEqual(["linked"]);
    expect(ids("github.com:vibepod")).toEqual(["linked"]);
    expect(ids("/srv/cli")).toEqual(["loose"]);
    expect(ids("gitlab")).toEqual(["loose"]);
  });
});
