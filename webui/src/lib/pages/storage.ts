// The Storage page's reads (routes/storage/+page.ts), in the order of StorageLists.loadData: the
// disks first and alone, since listing the pools and the filesystems found reads every disk and can
// spin a sleeping one up, which the disks' power read must not see (ADR-0378); then the pools and
// the filesystems found; then the scrub, the balance and the disk removal of each mounted pool,
// one after the other.
import {
	getBalanceStatus,
	getDiskRemoval,
	getDisks,
	getImportableFilesystems,
	getPools,
	getScrubStatus,
	type BalanceStatus,
	type Disk,
	type DiskRemovalStatus,
	type ImportableFilesystem,
	type Pool,
	type ScrubStatus
} from '$lib/api/storage';
import type { PageReader } from './load';
import { settle, valueOr, type Settled } from './read';

/** The status of a kind of job on each mounted pool, as read. */
export type JobReads<Status> = { pool: string; status: Settled<Status> }[];

export type StorageSnapshot = {
	disks: Settled<Disk[]>;
	pools: Settled<Pool[]>;
	importable: Settled<ImportableFilesystem[]>;
	scrubs: JobReads<ScrubStatus>;
	balances: JobReads<BalanceStatus>;
	removals: JobReads<DiskRemovalStatus>;
};

async function each<Status>(pools: string[], read: (pool: string) => Promise<Status>): Promise<JobReads<Status>> {
	const reads: JobReads<Status> = [];
	for (const pool of pools) reads.push({ pool, status: await settle(read(pool)) });
	return reads;
}

async function readStorage(): Promise<StorageSnapshot> {
	const disks = await settle(getDisks());
	const found = settle(getImportableFilesystems());
	const pools = await settle(getPools());
	const listed = valueOr(pools, []);
	const mounted = (Array.isArray(listed) ? listed : []).filter((pool) => pool.mounted).map((pool) => pool.name);
	const scrubs = await each(mounted, getScrubStatus);
	const balances = await each(mounted, getBalanceStatus);
	const removals = await each(mounted, getDiskRemoval);
	return { disks, pools, importable: await found, scrubs, balances, removals };
}

export const storageReader: PageReader<StorageSnapshot> = {
	key: 'storage',
	// The pools' use changes with files and snapshots.
	areas: ['storage', 'files', 'snapshots'],
	adminOnly: true,
	read: readStorage
};
