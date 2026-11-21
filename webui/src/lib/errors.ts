// The message of what a request or a browser API threw: an ApiError's is the server's (ADR-0189).
export function messageOf(e: unknown): string {
	return e instanceof Error ? e.message : String(e);
}

// What makes a message technical (ADR-0432): an errno, a traceback, an exception's name or repr.
// A page leads with a plain sentence of its catalog and keeps such a message folded under
// "Technical details" (ErrorState, TechDetails). A long sentence of the server stays in view.
const TECHNICAL =
	/\[Errno \d+\]|Traceback \(most recent call last\)|\b\w+(?:Error|Exception)\(|^[\w.]+(?:Error|Exception):/;

/** Whether `message` reads as technical text rather than as a sentence for people. */
export function isTechnical(message: string): boolean {
	return TECHNICAL.test(message);
}
