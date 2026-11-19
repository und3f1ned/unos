// The entries of the audit log that the page shows (ADR-0401), with their loading and error: a
// change of the filters loads the newest entries that match, after a pause in the typing, and
// Show more loads more of them. The page creates it and passes it down.
import {
	getAuditActions,
	getAuditCount,
	getAuditLogs,
	type AuditAction,
	type AuditEntry,
	type AuditFilters
} from '$lib/api/audit';

// Entries per "Show more", and how long typing may pause before the list follows it.
const PAGE_SIZE = 100;
const TYPING_DELAY_MS = 300;

function messageOf(e: unknown): string {
	return e instanceof Error ? e.message : String(e);
}

export class AuditLog {
	// The server's answers, each only ever replaced whole.
	actions = $state.raw<AuditAction[]>([]);
	actionsError = $state('');
	entries = $state.raw<AuditEntry[]>([]);
	total = $state(0);
	// loaded: an answer has arrived; loading: a request is out.
	loaded = $state(false);
	loading = $state(false);
	error = $state('');

	#limit = PAGE_SIZE;
	#timer: ReturnType<typeof setTimeout> | undefined;
	// The filters of the last scheduled load: spaces added around a value change nothing.
	#scheduled = '';
	// Each load gets a number: an answer that is not the newest request's is dropped.
	#latest = 0;

	/**
	 * Load the entries that match `current` from the first page: at once the first time, else
	 * after a pause in the typing. The same filters again load nothing.
	 */
	schedule(current: AuditFilters) {
		const key = JSON.stringify(current);
		if (key === this.#scheduled) return;
		this.#scheduled = key;
		clearTimeout(this.#timer);
		this.#timer = setTimeout(
			() => {
				this.#limit = PAGE_SIZE;
				void this.#load(current, this.#limit);
			},
			this.loaded ? TYPING_DELAY_MS : 0
		);
	}

	async #load(current: AuditFilters, size: number) {
		const request = ++this.#latest;
		this.loading = true;
		try {
			const [page, count] = await Promise.all([getAuditLogs(current, size), getAuditCount(current)]);
			if (request !== this.#latest) return;
			this.entries = page;
			this.total = count;
			this.error = '';
		} catch (e) {
			if (request !== this.#latest) return;
			// What was shown matched other filters.
			this.entries = [];
			this.total = 0;
			this.error = messageOf(e);
		} finally {
			if (request === this.#latest) {
				this.loading = false;
				this.loaded = true;
			}
		}
	}

	async loadActions() {
		try {
			this.actions = await getAuditActions();
			this.actionsError = '';
		} catch (e) {
			this.actionsError = messageOf(e);
		}
	}

	// From the newest entry with a larger limit rather than the next offset: entries written
	// since the last load would shift the pages and show an entry twice.
	showMore(current: AuditFilters) {
		this.#limit += PAGE_SIZE;
		void this.#load(current, this.#limit);
	}

	retry(current: AuditFilters) {
		void this.#load(current, this.#limit);
	}

	/** The page has gone: a scheduled load does not start. */
	dispose() {
		clearTimeout(this.#timer);
	}
}
