import type { SmbShareDeleteResult } from '$lib/api/shares';
import { toast } from '$lib/components/ui/toast';

/**
 * The outcome of the last change made on the shares page: what was done, as a toast (ADR-0432),
 * and, shown on the page until dismissed, the server's warnings, why a change failed, and what a
 * deletion of several shares left.
 */
export class ShareOutcome {
	warnings = $state<string[]>([]);
	error = $state('');
	// The shares of the last deletion of several that were not deleted, each with the reason.
	notDeleted = $state<SmbShareDeleteResult[]>([]);

	/** A change went through: its message, and the server's warnings in place of what was shown. */
	show(message: string, warnings: string[] | undefined) {
		this.notDeleted = [];
		toast.success(message);
		this.warnings = warnings ?? [];
		this.error = '';
	}
}
