/**
 * What the last change of a card did, shown until dismissed (OutcomeAlerts): the outcome, the
 * server's warnings about it, and a change refused after its dialog closed.
 */
export class Outcome {
	notice = $state('');
	warnings = $state.raw<string[]>([]);
	// `title` names what was refused; without one, OutcomeAlerts shows its `failureTitle`.
	failure = $state.raw<{ title?: string; message: string } | null>(null);

	show(message: string, warnings: string[] | undefined) {
		this.notice = message;
		this.warnings = warnings ?? [];
		this.failure = null;
	}

	fail(message: string, title?: string) {
		this.failure = title === undefined ? { message } : { title, message };
	}
}
