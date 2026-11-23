// The Snapshots page's reads (routes/snapshots/+page.ts), in the order of SnapshotLists.refresh:
// the mounted pools and their volumes, then the snapshots and the restored copies of the first one,
// which the page chooses when it opens. Another choice is the page's: it reads that one's lists itself.
import {
	getRestoreCopies,
	getSnapshots,
	getSubvolumes,
	type RestoreCopy,
	type SnapshotInfo,
	type Subvolume
} from '$lib/api/snapshots';
import type { PageReader } from './load';
import { settle, valueOr, type Settled } from './read';

export type SnapshotsSnapshot = {
	subvolumes: Settled<Subvolume[]>;
	/** The pool or volume whose lists were read: the first one offered; '' when none is. */
	path: string;
	snapshots: Settled<SnapshotInfo[]>;
	copies: Settled<RestoreCopy[]>;
};

const NONE: Settled<never[]> = { ok: true, value: [] };

async function readSnapshots(): Promise<SnapshotsSnapshot> {
	const subvolumes = await settle(getSubvolumes());
	const listed = valueOr(subvolumes, []);
	const path = (Array.isArray(listed) ? listed : [])[0]?.path ?? '';
	if (!path) return { subvolumes, path, snapshots: NONE, copies: NONE };
	const [snapshots, copies] = await Promise.all([settle(getSnapshots(path)), settle(getRestoreCopies(path))]);
	return { subvolumes, path, snapshots, copies };
}

export const snapshotsReader: PageReader<SnapshotsSnapshot> = {
	key: 'snapshots',
	// The pools and volumes offered are the mounted pools' (storage).
	areas: ['snapshots', 'storage'],
	adminOnly: true,
	read: readSnapshots
};
