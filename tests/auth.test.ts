import { describe, expect, it } from "vitest";

import {
  createAdminSessionManager,
  createRawApiToken,
  hashApiToken,
  parseBearerToken
} from "../src/server/auth.js";

describe("auth helpers", () => {
  it("creates and validates admin sessions", () => {
    const sessions = createAdminSessionManager({
      username: "admin",
      password: "secret"
    });

    expect(sessions.login("admin", "wrong")).toBeNull();
    const session = sessions.login("admin", "secret");

    expect(session?.username).toBe("admin");
    expect(session?.cookie).toContain("vibepod_session=");
    expect(sessions.authenticateCookie(session?.cookie ?? "")?.username).toBe("admin");
    sessions.logout(session?.id ?? "");
    expect(sessions.authenticateCookie(session?.cookie ?? "")).toBeNull();
  });

  it("generates hashable bearer tokens", () => {
    const token = createRawApiToken();

    expect(token).toMatch(/^vbp_[A-Za-z0-9_-]{43}$/);
    expect(hashApiToken(token)).toHaveLength(64);
    expect(hashApiToken(token)).toBe(hashApiToken(token));
  });

  it("parses bearer tokens", () => {
    expect(parseBearerToken("Bearer vbp_abc")).toBe("vbp_abc");
    expect(parseBearerToken("Basic abc")).toBeNull();
    expect(parseBearerToken(undefined)).toBeNull();
  });
});
