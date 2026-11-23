// The lists of the Snapshots page: the mounted pools and their volumes (ADR-0243), the one chosen,
// and its snapshots and the copies that restores made right in it (ADR-0358).
import {
	getRestoreCopies,
	getSnapshots,
	getSubvolumes,
	type RestoreCopy,
	type SnapshotInfo,
	type Subvolume
} from '$lib/api/snapshots';
import type { SnapshotsSnapshot } from '$lib/pages/snapshots';
import type { Settled } from '$lib/pages/read';
import { messageOf } from './names';

/** A list of the chosen pool or volume, with its load and why it failed. */
export class PathList<Item> {
	items: Item[] = $state.raw([]);
	loading = $state(false);
	error = $state('');
	// The path whose answer the list shows: a reload of the same path keeps it on the page, a load
	// of another one shows its skeleton (ADR-0430).
	shownPath = $state<string | null>(null);
	// Only the latest request updates the list: another pool or volume may have been chosen meanwhile.
	#request = 0;
	readonly #read: (path: string) => Promise<Item[]>;

	constructor(read: (path: string) => Promise<Item[]>) {
		this.#read = read;
	}

	async load(path: string) {
		const request = ++this.#request;
		if (!path) {
			this.items = [];
			this.error = '';
			this.shownPath = path;
			this.loading = false;
			return;
		}
		this.loading = true;
		try {
			const list = await this.#read(path);
			if (request === this.#request) {
				this.items = list;
				this.shownPath = path;
				this.error = '';
			}
		} catch (e) {
			if (request === this.#request) {
				this.items = [];
				this.shownPath = path;
				this.error = messageOf(e);
			}
		} finally {
			if (request === this.#request) this.loading = false;
		}
	}

	/** The list of `path` as the page's load read it ($lib/pages/snapshots), as a load that has answered. */
	show(path: string, read: Settled<Item[]>) {
		++this.#request;
		this.items = read.ok && Array.isArray(read.value) ? read.value : [];
		this.error = read.ok ? '' : read.error;
		this.shownPath = path;
		this.loading = false;
	}
}

export class SnapshotLists {
	subvolumes: Subvolume[] = $state.raw([]);
	subvolumesLoading = $state(true);
	subvolumesError = $state('');
	// The path of the pool or volume whose snapshots are listed.
	selectedPath = $state('');

	readonly snapshots = new PathList<SnapshotInfo>(getSnapshots);
	readonly copies = new PathList<RestoreCopy>(getRestoreCopies);

	// Pools offered without their volumes, which could not be read (a disk that dropped off, say).
	unreadVolumes = $derived(this.subvolumes.filter((sv) => sv.volumes_error));
	selected = $derived(this.subvolumes.find((sv) => sv.path === this.selectedPath) ?? null);
	busy = $derived(this.subvolumesLoading || this.snapshots.loading || this.copies.loading);

	// The page's own loads so far (a choice, a reload after a change), and their count when
	// the last snapshot of its load was shown.
	#loads = 0;
	#appliedAt = 0;

	/**
	 * The lists of one snapshot of the page's load ($lib/pages/snapshots), as a refresh that has
	 * answered. Not after a load of the page's own started since the last snapshot: it read later.
	 * The choice stays while it is offered; one other than the snapshot's reads its own lists.
	 */
	apply(snapshot: SnapshotsSnapshot) {
		if (this.#loads !== this.#appliedAt) return;
		const { subvolumes } = snapshot;
		this.subvolumes = subvolumes.ok && Array.isArray(subvolumes.value) ? subvolumes.value : [];
		this.subvolumesError = subvolumes.ok ? '' : subvolumes.error;
		this.subvolumesLoading = false;
		if (!this.subvolumes.some((sv) => sv.path === this.selectedPath)) this.selectedPath = snapshot.path;
		if (this.selectedPath === snapshot.path) {
			this.snapshots.show(snapshot.path, snapshot.snapshots);
			this.copies.show(snapshot.path, snapshot.copies);
		} else {
			void Promise.all([this.loadSnapshots(), this.loadCopies()]);
		}
		this.#appliedAt = this.#loads;
	}

	/** The next snapshot of the page's load (its Refresh) is newer than the loads of its own so far. */
	expectSnapshot() {
		this.#appliedAt = this.#loads;
	}

	async refresh() {
		this.#loads += 1;
		this.subvolumesLoading = true;
		this.subvolumesError = '';
		try {
			this.subvolumes = await getSubvolumes();
		} catch (e) {
			this.subvolumes = [];
			this.subvolumesError = messageOf(e);
		} finally {
			this.subvolumesLoading = false;
		}
		// The choice stays while it is offered.
		if (!this.subvolumes.some((sv) => sv.path === this.selectedPath)) {
			this.selectedPath = this.subvolumes[0]?.path ?? '';
		}
		await Promise.all([this.loadSnapshots(), this.loadCopies()]);
	}

	loadSnapshots() {
		this.#loads += 1;
		return this.snapshots.load(this.selectedPath);
	}

	loadCopies() {
		this.#loads += 1;
		return this.copies.load(this.selectedPath);
	}
}
