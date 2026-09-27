// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CopyableCode } from "../src/client/CopyableCode.js";

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
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const wrap = (node: ReactNode) =>
  render(<MantineProvider>{node}</MantineProvider>);

const stubClipboard = () => {
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(window.navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  return writeText;
};

describe("CopyableCode", () => {
  it("copies the exact value of a code block and confirms", async () => {
    const user = userEvent.setup();
    const writeText = stubClipboard();
    const config = '{\n  "mcpServers": {}\n}';
    const { container } = wrap(
      <CopyableCode value={config} label="Codex config" />,
    );

    expect(container.querySelector("pre code")?.textContent).toBe(config);
    await user.click(screen.getByRole("button", { name: "Copy Codex config" }));

    expect(writeText).toHaveBeenCalledWith(config);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
  });

  it("copies an inline value such as an endpoint URL", async () => {
    const user = userEvent.setup();
    const writeText = stubClipboard();
    const url = "http://localhost:3000/mcp";
    wrap(<CopyableCode value={url} label="MCP URL" variant="inline" />);

    expect(screen.getByText(url)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Copy MCP URL" }));

    expect(writeText).toHaveBeenCalledWith(url);
  });
});
