// System > Scheduler's reads (routes/system/scheduler/+page.ts, ADR-0264): the tasks, and the pools
// and disks that a task's form offers, which list every disk: the tab shows its tasks without
// waiting for them, and the form says so until they come.
import { getTasks, type ScheduledTask } from '$lib/api/scheduler';
import { getDisks, getPools, type Disk, type Pool } from '$lib/api/storage';
import type { PageReader } from '$lib/pages/load';
import { later, settle, type Later, type Settled } from '$lib/pages/read';

/** The pools and the whole disks; null when they could not be read. */
export type SchedulerResources = { pools: Pool[]; disks: Disk[] } | null;

export type SchedulerSnapshot = {
	tasks: Settled<ScheduledTask[]>;
	resources: Later<SchedulerResources>;
};

async function readResources(): Promise<SchedulerResources> {
	try {
		const [pools, disks] = await Promise.all([getPools(), getDisks()]);
		// The disks a SMART test can run on: an SD card or eMMC has no SMART (storage.free-mmc-as-data-disk).
		return { pools, disks: disks.filter((disk) => disk.type === 'disk' && !disk.mmc_type) };
	} catch {
		return null;
	}
}

async function readScheduler(): Promise<SchedulerSnapshot> {
	const resources = later(readResources(), null);
	const tasks = await settle(getTasks().then((answer) => (Array.isArray(answer) ? answer : [])));
	return { tasks, resources };
}

export const schedulerReader: PageReader<SchedulerSnapshot> = {
	key: 'system:scheduler',
	// The next runs follow the host's time zone (/system/timezone).
	areas: ['scheduler', 'storage', 'system'],
	adminOnly: true,
	read: readScheduler
};
