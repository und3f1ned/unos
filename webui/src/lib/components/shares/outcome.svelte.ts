import type { SmbShareDeleteResult } from '$lib/api/shares';

/**
 * The outcome of the last change made on the shares page, shown until dismissed: what was done,
 * the server's warnings, why a change failed, and what a deletion of several shares left.
 */
export class ShareOutcome {
	notice = $state('');
	warnings = $state<string[]>([]);
	error = $state('');
	// The shares of the last deletion of several that were not deleted, each with the reason.
	notDeleted = $state<SmbShareDeleteResult[]>([]);

	/** A change went through: its message and the server's warnings replace what was shown. */
	show(message: string, warnings: string[] | undefined) {
		this.notDeleted = [];
		this.notice = message;
		this.warnings = warnings ?? [];
		this.error = '';
	}
}
