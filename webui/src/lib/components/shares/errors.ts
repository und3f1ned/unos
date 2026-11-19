/** What a failed request says, for an alert: an ApiError's message is the server's text. */
export function messageOf(e: unknown): string {
	return e instanceof Error ? e.message : String(e);
}
