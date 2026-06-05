import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import type { Request, Response } from "express";

type AdminCredentials = {
  username: string;
  password: string;
};

type AdminSession = {
  id: string;
  username: string;
  createdAt: number;
};

const sessionCookieName = "vibepod_session";

export const createRawApiToken = () => `vbp_${randomBytes(32).toString("base64url")}`;

export const hashApiToken = (token: string) =>
  createHash("sha256").update(token, "utf8").digest("hex");

export const parseBearerToken = (authorization: string | undefined): string | null => {
  if (!authorization?.startsWith("Bearer ")) {
    return null;
  }
  const token = authorization.slice("Bearer ".length).trim();
  return token || null;
};

export const createAdminSessionManager = (credentials: AdminCredentials) => {
  const sessions = new Map<string, AdminSession>();

  const matches = (actual: string, expected: string) => {
    const actualBuffer = Buffer.from(actual);
    const expectedBuffer = Buffer.from(expected);
    return actualBuffer.length === expectedBuffer.length && timingSafeEqual(actualBuffer, expectedBuffer);
  };

  return {
    login(username: string, password: string) {
      if (!matches(username, credentials.username) || !matches(password, credentials.password)) {
        return null;
      }
      const id = randomBytes(32).toString("base64url");
      const session = { id, username, createdAt: Date.now() };
      sessions.set(id, session);
      return {
        id,
        username,
        cookie: `${sessionCookieName}=${id}; HttpOnly; SameSite=Lax; Path=/`
      };
    },
    authenticateCookie(cookieHeader: string | undefined) {
      const id = cookieHeader
        ?.split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith(`${sessionCookieName}=`))
        ?.slice(sessionCookieName.length + 1);
      return id ? sessions.get(id) ?? null : null;
    },
    logout(id: string) {
      sessions.delete(id);
    },
    clearCookie: `${sessionCookieName}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0`
  };
};

export type AdminSessionManager = ReturnType<typeof createAdminSessionManager>;

export const setSessionCookie = (res: Response, cookie: string) => {
  res.setHeader("Set-Cookie", cookie);
};

export const sessionFromRequest = (req: Request, sessions: AdminSessionManager) =>
  sessions.authenticateCookie(req.headers.cookie);
