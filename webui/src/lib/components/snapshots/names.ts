// Names on the disk, the same in every language (ADR-0296): the folder of a pool or a volume
// that holds its snapshots, and the prefixes of the names that UNOS gives, which a name's
// pattern puts before its translated <date>_<time>. They are the server's
// (supervisor/tests/test_snapshots.py), and no text of the catalog holds one.
export const SNAPSHOTS_FOLDER = '.snapshots';
export const SNAP_PREFIX = 'snap_';
export const RESTORE_PREFIX = 'restore_';
export const AUTO_PREFIX = 'auto_';

export function messageOf(e: unknown): string {
	return e instanceof Error ? e.message : String(e);
}
