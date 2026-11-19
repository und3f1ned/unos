// The message of what a request or a browser API threw: an ApiError's is the server's (ADR-0189).
export function messageOf(e: unknown): string {
	return e instanceof Error ? e.message : String(e);
}
