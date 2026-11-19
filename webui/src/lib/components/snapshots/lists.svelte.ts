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
import { messageOf } from './names';

/** A list of the chosen pool or volume, with its load and why it failed. */
export class PathList<Item> {
	items: Item[] = $state.raw([]);
	loading = $state(false);
	error = $state('');
	// Only the latest request updates the list: another pool or volume may have been chosen meanwhile.
	#request = 0;
	readonly #read: (path: string) => Promise<Item[]>;

	constructor(read: (path: string) => Promise<Item[]>) {
		this.#read = read;
	}

	async load(path: string) {
		const request = ++this.#request;
		this.error = '';
		if (!path) {
			this.items = [];
			this.loading = false;
			return;
		}
		this.loading = true;
		try {
			const list = await this.#read(path);
			if (request === this.#request) this.items = list;
		} catch (e) {
			if (request === this.#request) {
				this.items = [];
				this.error = messageOf(e);
			}
		} finally {
			if (request === this.#request) this.loading = false;
		}
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

	async refresh() {
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
		return this.snapshots.load(this.selectedPath);
	}

	loadCopies() {
		return this.copies.load(this.selectedPath);
	}
}
