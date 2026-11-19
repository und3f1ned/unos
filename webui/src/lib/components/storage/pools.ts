// What the components of the Storage page share, without texts: where a pool is mounted, which
// jobs run on it (scrub ADR-0313, balance ADR-0379, disk removal ADR-0411) and how far they are.
// The texts are the components' own, from $t (ADR-0287).
import type { BalanceStatus, DiskRemovalStatus, Pool, ScrubStatus, Volume } from '$lib/api/storage';

/** The variants of the page's badges. */
export type BadgeVariant = 'success' | 'warning' | 'error' | 'info' | 'neutral' | 'orange';

export { messageOf } from '$lib/errors';

/** Where UNOS mounts the pool of this name. */
export function poolMountpoint(name: string): string {
	return `/mnt/pools/${name}`;
}

// The statuses of a balance that runs: a pause or a cancel may still wait for its chunk.
const BALANCE_RUNS = ['running', 'pausing', 'cancelling'];

export const balanceRuns = (status: BalanceStatus | undefined): boolean =>
	status !== undefined && BALANCE_RUNS.includes(status.status);

export const removalRuns = (status: DiskRemovalStatus | undefined): boolean => status?.status === 'running';

/** What the buttons of a balance that runs or is paused ask the server for. */
export type BalanceAction = 'pause' | 'resume' | 'cancel';

/** Neither an add nor a removal beside a scrub, a balance or a removal, nor on a pool that cannot change. */
export function diskChangeBlocked(
	pool: Pool,
	scrub: ScrubStatus | undefined,
	balance: BalanceStatus | undefined,
	removal: DiskRemovalStatus | undefined
): boolean {
	return (
		pool.read_only ||
		pool.missing_devices > 0 ||
		scrub?.status === 'running' ||
		(balance !== undefined && balance.status !== 'idle') ||
		removalRuns(removal)
	);
}

/** What the Mount / Unmount dialog asks for. */
export type PoolAction = { kind: 'mount' | 'unmount'; pool: Pool };

/** What a pool's card asks the page for: each callback gets the pool of the card. */
export type PoolHandlers = {
	onscrub: (pool: Pool) => void;
	oncancelscrub: (pool: Pool) => void;
	onbalance: (pool: Pool) => void;
	onbalanceaction: (pool: Pool, action: BalanceAction) => void;
	onadddisk: (pool: Pool) => void;
	onremovedisk: (pool: Pool) => void;
	onvolumes: (pool: Pool) => void;
	onmount: (pool: Pool) => void;
	onunmount: (pool: Pool) => void;
	ondelete: (pool: Pool) => void;
};

export function formatDuration(seconds: number): string {
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	const secs = Math.floor(seconds % 60);
	return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

export function getScrubProgress(status: ScrubStatus): number {
	if (!status.bytes_scrubbed || !status.total_bytes || status.total_bytes === 0) {
		return 0;
	}
	return (status.bytes_scrubbed / status.total_bytes) * 100;
}

export function balanceProgress(status: BalanceStatus): number {
	if (!status.expected || status.completed === null) return 0;
	return Math.min(100, (status.completed / status.expected) * 100);
}

export function removalProgress(status: DiskRemovalStatus): number {
	if (!status.total || status.left === null) return 0;
	return Math.min(100, Math.max(0, ((status.total - status.left) / status.total) * 100));
}

// The bar's colour follows the alerts' levels (supervisor/src/storage/volume_usage.py): a warning from 80% of
// the quota, an error from 95%. A quota of 0 bytes is full.
export function quotaVariant(volume: Volume): 'success' | 'warning' | 'error' {
	const used = volume.used ?? 0;
	const quota = volume.quota ?? 0;
	if (used * 100 >= 95 * quota) return 'error';
	return used * 100 >= 80 * quota ? 'warning' : 'success';
}
