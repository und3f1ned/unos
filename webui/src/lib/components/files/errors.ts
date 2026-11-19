import { ApiError } from "$lib/api/client";

// The server's refusals as the Files pages show them.

/** The reason of a failure: the server's message (ApiError), or what the browser said. */
export function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** A 401 has already ended the session and the sign-in page is on its way: nothing to show. */
export function signedOut(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401;
}
