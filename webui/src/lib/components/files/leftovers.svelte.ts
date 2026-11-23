import { listUploadLeftovers, removeUploadLeftovers, type UploadLeftover } from '$lib/api/files';
import type { Settled } from '$lib/pages/read';
import { messageOf, signedOut } from './errors';

/**
 * The hidden files that uploads left when the supervisor stopped in the middle of them, as its
 * last check found them (ADR-0354): the notice above the list offers to remove them. The Remove
 * dialog: the files it is about while it is open, whether the removal is on its way, and the
 * server's refusal.
 */
export class UploadLeftovers {
	files = $state.raw<UploadLeftover[]>([]);
	removing = $state.raw<UploadLeftover[] | null>(null);
	sending = $state(false);
	error = $state('');
	// The first answer has come (or failed): the page draws the notice with the list (ADR-0430).
	loaded = $state(false);
	size = $derived(this.files.reduce((sum, file) => sum + file.size, 0));

	// The loads of its own (after a removal), and their count when the last snapshot of the page's
	// load was shown: a snapshot comes second to a load of its own that started after it.
	#loads = 0;
	#appliedAt = 0;

	/** The files as the page's load read them ($lib/pages/files), unless a load of its own started since. */
	apply(read: Settled<UploadLeftover[]>) {
		if (this.#loads !== this.#appliedAt) return;
		this.files = read.ok ? read.value : [];
		this.loaded = true;
	}

	/** The next snapshot of the page's load (its Refresh) is newer than the loads of its own so far. */
	expectSnapshot() {
		this.#appliedAt = this.#loads;
	}

	// Without an answer the page shows no notice; the rest of it works as before.
	async load() {
		this.#loads += 1;
		try {
			this.files = (await listUploadLeftovers()).files;
		} catch {
			this.files = [];
		} finally {
			this.loaded = true;
		}
	}

	/** Opens the dialog about the files listed now. */
	ask() {
		this.removing = this.files;
		this.error = '';
	}

	/**
	 * Removes the files of the dialog. The server checks every file before it removes any: a refusal
	 * keeps the dialog open with its reason, about the files still listed. The server drops from its
	 * list the file it refused and those that are gone, so Remove for good does not meet the same
	 * refusal again; with none left, the dialog closes and the answer is "unlisted", for the page to
	 * show the reason above the list. "signed-out": a 401 has ended the session.
	 */
	async remove(): Promise<'done' | 'unlisted' | 'signed-out'> {
		const files = this.removing;
		if (!files) return 'done';
		this.error = '';
		this.sending = true;
		try {
			await removeUploadLeftovers(files.map((file) => file.path));
			this.removing = null;
		} catch (e) {
			if (signedOut(e)) return 'signed-out';
			this.error = messageOf(e);
		} finally {
			this.sending = false;
		}
		await this.load();
		if (this.removing === null) return 'done';
		if (this.files.length > 0) {
			this.removing = this.files;
			return 'done';
		}
		this.removing = null;
		return 'unlisted';
	}
}
