import { describe, expect, it } from "vitest";

import { formatTaskKey } from "../src/shared/dependencies.js";
import { parseTaskReference } from "../src/shared/taskKeys.js";

describe("task keys", () => {
  it("parses a project key and task number", () => {
    expect(parseTaskReference("VP-236")).toEqual({
      kind: "key",
      projectKey: "VP",
      taskNumber: 236,
    });
  });

  it("uppercases the project key and tolerates surrounding space", () => {
    expect(parseTaskReference("  vp-236 ")).toEqual({
      kind: "key",
      projectKey: "VP",
      taskNumber: 236,
    });
  });

  it("parses a bare task number", () => {
    expect(parseTaskReference("236")).toEqual({
      kind: "number",
      taskNumber: 236,
    });
  });

  it("treats anything else as an opaque id", () => {
    expect(parseTaskReference("0484e578-aab9-4678-9301-7ebb2d67de5b")).toEqual({
      kind: "id",
    });
    expect(parseTaskReference("VP-236-2")).toEqual({ kind: "id" });
    expect(parseTaskReference("SVEN-1")).toEqual({ kind: "id" });
    expect(parseTaskReference("VP-0")).toEqual({ kind: "id" });
    expect(parseTaskReference("VP-01")).toEqual({ kind: "id" });
    expect(parseTaskReference("")).toEqual({ kind: "id" });
  });

  it("round-trips with formatTaskKey", () => {
    expect(parseTaskReference(formatTaskKey("VP", 236))).toEqual({
      kind: "key",
      projectKey: "VP",
      taskNumber: 236,
    });
  });
});
