// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MantineProvider, Textarea } from "@mantine/core";
import { render, screen } from "@testing-library/react";
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
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

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
  const clientEntry = readFileSync(
    join(process.cwd(), "src/client/main.tsx"),
    "utf8",
  );

  it("wraps both task fields in a markdown edit/preview switch", () => {
    expect(clientEntry).toContain(
      '<MarkdownField\n                label="Description"',
    );
    expect(clientEntry).toContain(
      '<MarkdownField\n                label="Acceptance Criteria"',
    );
    expect(clientEntry).toContain("mode={descriptionMode}");
    expect(clientEntry).toContain("mode={criteriaMode}");
  });

  it("renders the task overview sections as markdown", () => {
    expect(clientEntry).toContain("<MarkdownText>");
    expect(clientEntry).toContain(
      "<InlineMarkdown>{criterion}</InlineMarkdown>",
    );
  });

  it("declares the markdown rendering packages", () => {
    const packageJson = JSON.parse(
      readFileSync(join(process.cwd(), "package.json"), "utf8"),
    ) as { dependencies?: Record<string, string> };
    expect(packageJson.dependencies).toHaveProperty("react-markdown");
    expect(packageJson.dependencies).toHaveProperty("remark-gfm");
  });
});
