// The server's refusals as the Users page shows them.

/** The reason of a failure: the server's message (ApiError), or what the browser said. */
export function messageOf(e: unknown): string {
	return e instanceof Error ? e.message : String(e);
}
