import { ApiError } from '$lib/api/client';
import {
	clearImportReport,
	getSmbShares,
	getSmbStatus,
	updateSmbShare,
	type SmbServiceStatus,
	type SmbShare
} from '$lib/api/shares';
import { messageOf } from './errors';
import type { ShareOutcome } from './outcome.svelte';

/** A share as a selection holds it: share names cannot hold a line break. */
export function keyOf(share: SmbShare): string {
	return `${share.name}\n${share.path}`;
}

// The most shares one deletion takes (BULK_DELETE_MAX of supervisor/src/shares/schemas.py).
export const BULK_DELETE_MAX = 200;

/**
 * The SMB shares and the status of the SMB service as the page last read them, the selection of
 * several shares, and the changes made from the list. The page creates it and passes it down.
 */
export class SmbShares {
	shares = $state<SmbShare[]>([]);
	error = $state('');
	status = $state<SmbServiceStatus | null>(null);
	statusError = $state('');
	// The shares whose import notes are being dismissed, by name.
	dismissing = $state<Record<string, boolean>>({});

	// Several shares at once (ADR-0389): the selected shares, each by its name and its folder
	// (keyOf). A share the list no longer has with both leaves the selection whenever the list is
	// loaded again: a selection never moves to a share made again under the same name in another
	// folder. Always replaced, never changed inside: the page reacts to a new set.
	selected = $state(new Set<string>());
	// A deletion of several runs: the boxes of the selection and its button wait for it.
	bulkDeleting = $state(false);

	allSelected = $derived(this.shares.length > 0 && this.shares.every((share) => this.selected.has(keyOf(share))));

	#outcome: ShareOutcome;

	/** `outcome` takes the errors and the warnings of the changes made from the list. */
	constructor(outcome: ShareOutcome) {
		this.#outcome = outcome;
	}

	async load() {
		try {
			this.shares = await getSmbShares();
			this.error = '';
			const listed = new Set(this.shares.map(keyOf));
			this.selected = new Set([...this.selected].filter((key) => listed.has(key)));
		} catch (e) {
			this.error = messageOf(e);
		}
	}

	async loadStatus() {
		try {
			this.status = await getSmbStatus();
			this.statusError = '';
		} catch (e) {
			this.status = null;
			this.statusError = messageOf(e);
		}
	}

	/** The shares, then the status. */
	async reload() {
		await this.load();
		await this.loadStatus();
	}

	select(share: SmbShare, on: boolean) {
		const next = new Set(this.selected);
		if (on) next.add(keyOf(share));
		else next.delete(keyOf(share));
		this.selected = next;
	}

	selectAll(on: boolean) {
		this.selected = on ? new Set(this.shares.map(keyOf)) : new Set();
	}

	/** The selected shares, in the order of the list. */
	chosen(): SmbShare[] {
		return this.shares.filter((share) => this.selected.has(keyOf(share)));
	}

	async dismissNotes(share: SmbShare) {
		this.dismissing = { ...this.dismissing, [share.name]: true };
		this.#outcome.error = '';
		try {
			const updated = await updateSmbShare(share.name, {
				import_notes: [],
				expected_version: share.version
			});
			this.shares = this.shares.map((s) => (s.name === share.name ? updated : s));
			if (updated.warnings?.length) this.#outcome.warnings = updated.warnings;
		} catch (e) {
			this.#outcome.error = messageOf(e);
			// 409: the share was changed meanwhile; show it as it is now.
			if (e instanceof ApiError && e.status === 409) await this.load();
		} finally {
			this.dismissing = { ...this.dismissing, [share.name]: false };
		}
	}

	async dismissImportReport() {
		this.#outcome.error = '';
		try {
			await clearImportReport();
			if (this.status) this.status = { ...this.status, import_report: [] };
		} catch (e) {
			this.#outcome.error = messageOf(e);
		}
	}
}
