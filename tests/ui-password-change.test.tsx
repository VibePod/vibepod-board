// @vitest-environment jsdom

import { MantineProvider } from "@mantine/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PasswordChangeForm,
  passwordProblem,
} from "../src/client/PasswordChange.js";

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

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const fill = async (current: string, next: string, repeat: string) => {
  await userEvent.type(screen.getByLabelText("Current password"), current);
  await userEvent.type(screen.getByLabelText("New password"), next);
  await userEvent.type(screen.getByLabelText("Repeat new password"), repeat);
  await userEvent.click(screen.getByRole("button", { name: "Set Password" }));
};

const renderForm = () => {
  const onChanged = vi.fn();
  render(
    <MantineProvider>
      <PasswordChangeForm onChanged={onChanged} />
    </MantineProvider>,
  );
  return onChanged;
};

describe("passwordProblem", () => {
  it("asks for twelve characters and two equal entries", () => {
    expect(passwordProblem("short", "short")).toBe(
      "The new password needs at least 12 characters.",
    );
    expect(passwordProblem("a long password", "a long passwor")).toBe(
      "The two new passwords differ.",
    );
    expect(passwordProblem("a long password", "a long password")).toBeNull();
  });
});

describe("PasswordChangeForm", () => {
  it("sends the current and the new password and reports success", async () => {
    const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) =>
      json({ authenticated: true }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onChanged = renderForm();

    await fill("temporary-pass-1", "a long password", "a long password");

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/auth/password");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({
      currentPassword: "temporary-pass-1",
      newPassword: "a long password",
    });
  });

  it("does not send a password that is mistyped", async () => {
    const fetchMock = vi.fn(async () => json({ authenticated: true }));
    vi.stubGlobal("fetch", fetchMock);
    const onChanged = renderForm();

    await fill("temporary-pass-1", "a long password", "another password");

    expect(
      await screen.findByText("The two new passwords differ."),
    ).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("shows what the server refused and can be sent again", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ error: "Current password is wrong" }, 403)),
    );
    const onChanged = renderForm();

    await fill("wrong", "a long password", "a long password");

    expect(await screen.findByText("Current password is wrong")).toBeTruthy();
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Set Password" })).toHaveProperty(
      "disabled",
      false,
    );
  });
});
