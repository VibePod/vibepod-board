import { readFileSync } from "node:fs";
import { join } from "node:path";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const page = readFileSync(
  join(process.cwd(), "backend/vibepod_board/static/mcp-docs.html"),
  "utf8",
);

const catalog = {
  server: { name: "vibepod-board", version: "9.9.9" },
  endpoint: "/mcp",
  transport: "streamable-http",
  auth: "bearer",
  tools: [
    {
      name: "list_things",
      title: "List Things",
      description: "Lists things.",
      inputSchema: { type: "object", properties: {} },
      outputSchema: { type: "object" },
      annotations: { readOnlyHint: true },
    },
    {
      name: "claim_thing",
      title: "Claim Thing",
      description: "Claims <b>one</b> thing.",
      inputSchema: {
        type: "object",
        required: ["projectId"],
        properties: {
          projectId: {
            type: "string",
            minLength: 1,
            description: "Project id.",
          },
          labels: {
            anyOf: [
              { type: "array", items: { type: "string" } },
              { type: "null" },
            ],
            default: null,
            description: "Only these labels.",
          },
          limit: { type: "integer", minimum: 1, maximum: 50, default: 10 },
          mode: { enum: ["fast", "safe"], type: "string", default: "safe" },
          task: {
            anyOf: [{ type: "string", minLength: 1 }, { type: "null" }],
            default: null,
          },
          mixed: {
            type: "array",
            items: { anyOf: [{ type: "string" }, { type: "integer" }] },
          },
        },
      },
    },
    {
      name: "delete_thing",
      title: "Delete Thing",
      description: "Removes a thing for good.",
      inputSchema: {
        type: "object",
        properties: { id: { type: "string" } },
        required: ["id"],
      },
      annotations: { destructiveHint: true, idempotentHint: true },
    },
  ],
  resources: [
    {
      uri: "vibepod-board://state",
      name: "board-state",
      description: "Full state as JSON.",
    },
  ],
};

type CatalogResponse = {
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
};

const found: CatalogResponse = {
  ok: true,
  status: 200,
  json: async () => catalog,
};

/** Opens the page in jsdom with `fetch` answered by `respond`, once the request settled. */
const openPage = async (
  hash = "",
  respond: () => CatalogResponse = () => found,
) => {
  const requests: string[] = [];
  const { window } = new JSDOM(page, {
    runScripts: "dangerously",
    url: `http://board.test/mcp-docs${hash}`,
    beforeParse(pageWindow) {
      Object.assign(pageWindow, {
        fetch: async (path: string) => {
          requests.push(path);
          return respond();
        },
      });
    },
  });
  const { document } = window;
  const status = document.getElementById("status") as HTMLElement;
  await vi.waitFor(() => {
    expect(status.hidden || status.querySelector("button")).toBeTruthy();
  });
  return { window, document, status, requests };
};

const text = (document: Document, selector: string) =>
  document.querySelector(selector)?.textContent;

const texts = (document: Document, selector: string) =>
  [...document.querySelectorAll(selector)].map((node) => node.textContent);

const toolNames = (document: Document) => texts(document, "#tool-list a code");

const currentTool = (document: Document) =>
  document.querySelector("#tool-list a[aria-current]")?.getAttribute("href");

describe("MCP reference page", () => {
  it("loads the catalog and shows the first tool", async () => {
    const { document, status, requests } = await openPage();

    expect(requests).toEqual(["/api/mcp-tools"]);
    expect(status.hidden).toBe(true);
    expect(document.getElementById("layout")?.hidden).toBe(false);
    expect(text(document, "#server-version")).toBe("9.9.9");
    expect(text(document, "#count")).toBe("3 tools");
    expect(toolNames(document)).toEqual([
      "list_things",
      "claim_thing",
      "delete_thing",
    ]);
    expect(texts(document, "#tool-list .badge")).toEqual([
      "read-only",
      "destructive",
      "idempotent",
    ]);
    expect(currentTool(document)).toBe("#list_things");
    expect(text(document, "#detail h2")).toBe("List Things");
    expect(text(document, "#detail .muted")).toBe("No parameters.");
    expect(texts(document, "#detail summary")).toEqual([
      "Input schema",
      "Output schema",
    ]);
    expect(text(document, "#resource-list code")).toBe("vibepod-board://state");
    expect(text(document, "#resource-list .muted")).toBe(
      "board-state — Full state as JSON.",
    );
  });

  it("opens the tool named in the hash and describes its parameters", async () => {
    const { document } = await openPage("#claim_thing");

    expect(text(document, "#detail h2")).toBe("Claim Thing");
    const rows = [...document.querySelectorAll("#detail tbody tr")].map((row) =>
      [...row.children].map((cell) => cell.textContent),
    );
    expect(rows).toEqual([
      ["projectId", "string", "yes", "", "Project id.min length 1"],
      ["labels", "string[]", "no", "null", "Only these labels."],
      ["limit", "integer", "no", "10", "min 1, max 50"],
      ["mode", '"fast" | "safe"', "no", '"safe"', ""],
      ["task", "string", "no", "null", "min length 1"],
      ["mixed", "(string | integer)[]", "no", "", ""],
    ]);
    expect(texts(document, "#detail summary")).toEqual(["Input schema"]);
  });

  it("shows catalog text as text, never as markup", async () => {
    const { document } = await openPage("#claim_thing");

    expect(text(document, "#detail .description")).toBe(
      "Claims <b>one</b> thing.",
    );
    expect(document.querySelector("#detail .description b")).toBeNull();
  });

  it("falls back to the first tool for an unknown hash and follows the hash", async () => {
    const { window, document } = await openPage("#no_such_tool");
    expect(text(document, "#detail h2")).toBe("List Things");

    window.location.hash = "#delete_thing";
    await vi.waitFor(() => {
      expect(text(document, "#detail h2")).toBe("Delete Thing");
    });
    expect(currentTool(document)).toBe("#delete_thing");

    document
      .querySelector<HTMLAnchorElement>('#tool-list a[href="#claim_thing"]')
      ?.click();
    await vi.waitFor(() => {
      expect(text(document, "#detail h2")).toBe("Claim Thing");
    });
  });

  it("filters tools by name, title and description", async () => {
    const { window, document } = await openPage();
    const filter = document.getElementById("filter") as HTMLInputElement;
    const type = (value: string) => {
      filter.value = value;
      filter.dispatchEvent(new window.Event("input", { bubbles: true }));
    };

    type("FOR GOOD");
    expect(toolNames(document)).toEqual(["delete_thing"]);
    expect(text(document, "#count")).toBe("1 of 3 tools");
    expect(text(document, "#detail h2")).toBe("List Things");

    type("claim");
    expect(toolNames(document)).toEqual(["claim_thing"]);

    type("no match");
    expect(toolNames(document)).toEqual([]);
    expect(text(document, "#count")).toBe("0 of 3 tools");

    type("");
    expect(toolNames(document)).toHaveLength(3);
  });

  it("reports a failed request and loads again on Retry", async () => {
    const responses: CatalogResponse[] = [
      { ok: false, status: 503, json: async () => ({}) },
      found,
    ];
    const { document, status } = await openPage(
      "",
      () => responses.shift() ?? found,
    );

    expect(status.textContent).toContain(
      "Could not load the tool catalog (HTTP 503).",
    );
    expect(document.getElementById("layout")?.hidden).toBe(true);

    status.querySelector("button")?.click();
    await vi.waitFor(() => {
      expect(status.hidden).toBe(true);
    });
    expect(toolNames(document)).toHaveLength(3);
  });
});
