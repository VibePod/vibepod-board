/**
 * JSON request against the board API with the admin session cookie. A failed
 * request throws the server's error message.
 */
export const api = async <T>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as {
      error?: string;
    };
    throw new Error(error.error ?? `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
};
