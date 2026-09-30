/** A failed board API request: the server's error message and the HTTP status. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Whether a request was refused because the record changed since it was read. */
export const isConflict = (error: unknown): error is ApiError =>
  error instanceof ApiError && error.status === 409;

/**
 * JSON request against the board API with the admin session cookie. A failed
 * request throws an ApiError with the server's error message.
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
    throw new ApiError(
      error.error ?? `Request failed: ${response.status}`,
      response.status,
    );
  }
  return (await response.json()) as T;
};
