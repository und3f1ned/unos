/** The lines of a text field, trimmed, without the empty ones: one initiator name per line. */
export function lines(text: string): string[] {
	return text
		.split('\n')
		.map((line) => line.trim())
		.filter((line) => line.length > 0);
}
