// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MantineProvider, Textarea } from "@mantine/core";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactNode, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InlineMarkdown, MarkdownText } from "../src/client/Markdown.js";
import {
  CriteriaPreview,
  MarkdownField,
  MarkdownPreview,
} from "../src/client/MarkdownField.js";
import type { MarkdownFieldMode } from "../src/client/markdownField.js";
import type { BoardCard, Idea } from "../src/shared/types.js";

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  class ResizeObserverStub {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetModules();
});

const timestamp = "2026-08-14T12:00:00.000Z";
const markdownProject = {
  id: "project-1",
  key: "APP",
  title: "Markdown Project",
  summary: "",
  createdAt: timestamp,
  updatedAt: timestamp,
};
const markdownIdea: Idea = {
  id: "idea-1",
  projectId: markdownProject.id,
  taskNumber: 1,
  title: "Markdown task",
  summary: "",
  details: "## Goal\n\nRender **markdown**.",
  status: "ready",
  labels: [],
  acceptanceCriteria: ["Renders `code`"],
  dependsOn: [],
  blocks: [],
  blockedBy: [],
  createdAt: timestamp,
  updatedAt: timestamp,
};
const markdownCard: BoardCard = {
  id: "card-1",
  projectId: markdownProject.id,
  ideaId: markdownIdea.id,
  title: "Markdown card",
  details: "",
  column: "ready",
  labels: [],
  dependsOn: [],
  blockedBy: [],
  createdAt: timestamp,
  updatedAt: timestamp,
};

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

/** Renders the real app on a project section, so the wiring is exercised end to end. */
const renderTasksView = async (section: "tasks" | "board" = "tasks") => {
  window.history.replaceState(
    null,
    "",
    `/projects/${markdownProject.id}/${section}`,
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/auth/me") {
        return jsonResponse({ authenticated: true, username: "admin" });
      }
      if (path === "/api/projects") {
        return jsonResponse({ items: [markdownProject] });
      }
      if (path === "/api/ideas") {
        return jsonResponse({ items: [markdownIdea] });
      }
      if (path === "/api/documents") {
        return jsonResponse({ items: [] });
      }
      return jsonResponse({
        columns: {
          ready: [markdownCard],
          planned: [],
          in_progress: [],
          review: [],
          done: [],
        },
      });
    }),
  );
  const { AppShell } = await import("../src/client/main.js");
  render(<AppShell />);
  await screen.findByText(
    section === "tasks" ? "Markdown task" : "Markdown card",
  );
};

const renderWithMantine = (ui: ReactNode) =>
  render(<MantineProvider>{ui}</MantineProvider>);

const DescriptionHarness = ({ initial }: { initial: string }) => {
  const [value, setValue] = useState(initial);
  const [mode, setMode] = useState<MarkdownFieldMode>("edit");
  return (
    <MantineProvider>
      <MarkdownField
        label="Description"
        mode={mode}
        onModeChange={setMode}
        preview={
          <MarkdownPreview value={value} emptyText="No description yet." />
        }
      >
        <Textarea
          aria-label="Description"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      </MarkdownField>
    </MantineProvider>
  );
};

const CriteriaHarness = ({ initial }: { initial: string }) => {
  const [mode, setMode] = useState<MarkdownFieldMode>("edit");
  return (
    <MantineProvider>
      <MarkdownField
        label="Acceptance Criteria"
        mode={mode}
        onModeChange={setMode}
        preview={
          <CriteriaPreview
            value={initial}
            emptyText="No acceptance criteria yet."
          />
        }
      >
        <Textarea aria-label="Acceptance Criteria" defaultValue={initial} />
      </MarkdownField>
    </MantineProvider>
  );
};

describe("markdown task fields", () => {
  it("renders markdown blocks with headings, lists, and code", () => {
    renderWithMantine(
      <MarkdownText>
        {"# Goal\n\nShip **preview**.\n\n- one\n- two\n\n`npm test`"}
      </MarkdownText>,
    );

    expect(screen.getByRole("heading", { name: "Goal" })).toBeTruthy();
    expect(screen.getByText("preview").tagName).toBe("STRONG");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("npm test").tagName).toBe("CODE");
  });

  it("renders links safely without raw HTML", () => {
    renderWithMantine(
      <MarkdownText>
        {'[docs](https://example.com)\n\n<img src=x onerror="alert(1)">'}
      </MarkdownText>,
    );

    const link = screen.getByRole("link", { name: "docs" });
    expect(link.getAttribute("href")).toBe("https://example.com");
    expect(document.querySelector("img")).toBeNull();
  });

  it("renders inline markdown without a wrapping paragraph", () => {
    const { container } = renderWithMantine(
      <InlineMarkdown>{"Renders **bold** text"}</InlineMarkdown>,
    );

    expect(container.querySelector("p")).toBeNull();
    expect(screen.getByText("bold").tagName).toBe("STRONG");
  });

  it("switches the description between editing and a rendered preview", async () => {
    render(<DescriptionHarness initial={"## Goal\n\nRender **markdown**."} />);

    expect(screen.getByLabelText("Description")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Goal" })).toBeNull();

    await userEvent.click(screen.getByRole("radio", { name: "Preview" }));

    expect(screen.getByRole("heading", { name: "Goal" })).toBeTruthy();
    expect(screen.getByText("markdown").tagName).toBe("STRONG");
    expect(screen.queryByLabelText("Description")).toBeNull();

    await userEvent.click(screen.getByRole("radio", { name: "Edit" }));

    expect(screen.getByLabelText("Description")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Goal" })).toBeNull();
  });

  it("keeps edits made in the textarea when previewing", async () => {
    render(<DescriptionHarness initial="" />);

    await userEvent.type(screen.getByLabelText("Description"), "# Typed");
    await userEvent.click(screen.getByRole("radio", { name: "Preview" }));

    expect(screen.getByRole("heading", { name: "Typed" })).toBeTruthy();
  });

  it("previews acceptance criteria as the saved list with inline markdown", async () => {
    render(<CriteriaHarness initial={"Renders `code`\nToggle **persists**"} />);

    await userEvent.click(screen.getByRole("radio", { name: "Preview" }));

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(screen.getByText("code").tagName).toBe("CODE");
    expect(screen.getByText("persists").tagName).toBe("STRONG");
  });

  it("shows empty-state text instead of a blank preview", async () => {
    render(<DescriptionHarness initial="   " />);

    await userEvent.click(screen.getByRole("radio", { name: "Preview" }));

    expect(screen.getByText("No description yet.")).toBeTruthy();
  });
});

describe("task modal markdown wiring", () => {
  it("previews both task fields of the task modal as markdown", async () => {
    await renderTasksView();

    await userEvent.click(screen.getByText("Markdown task"));
    const dialog = await screen.findByRole("dialog");

    await userEvent.click(
      within(
        within(dialog).getByRole("radiogroup", { name: "Description view" }),
      ).getByRole("radio", { name: "Preview" }),
    );
    expect(within(dialog).getByRole("heading", { name: "Goal" })).toBeTruthy();
    expect(within(dialog).getByText("markdown").tagName).toBe("STRONG");

    await userEvent.click(
      within(
        within(dialog).getByRole("radiogroup", {
          name: "Acceptance Criteria view",
        }),
      ).getByRole("radio", { name: "Preview" }),
    );
    expect(within(dialog).getByText("code").tagName).toBe("CODE");
  });

  it("renders the task overview sections as markdown", async () => {
    await renderTasksView("board");

    await userEvent.click(screen.getByText("Markdown card"));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByRole("heading", { name: "Goal" })).toBeTruthy();
    expect(within(dialog).getByText("markdown").tagName).toBe("STRONG");
    expect(within(dialog).getByText("code").tagName).toBe("CODE");
  });

  it("declares the markdown rendering packages", () => {
    const packageJson = JSON.parse(
      readFileSync(join(process.cwd(), "package.json"), "utf8"),
    ) as { dependencies?: Record<string, string> };
    expect(packageJson.dependencies).toHaveProperty("react-markdown");
    expect(packageJson.dependencies).toHaveProperty("remark-gfm");
  });
});
