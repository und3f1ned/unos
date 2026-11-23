// System > Backup & Restore's reads (routes/system/backup/+page.ts, ADR-0165, ADR-0337): the
// configuration backups, and the off-device copy's settings and state and then the Scheduler's
// tasks that run it. The copy's snapshots are listed from its target by restic, a slow read that
// can reach another machine: the tab shows without waiting for them, their place waiting.
import { listBackups, type BackupInfo } from '$lib/api/backup';
import {
	getOffsiteSettings,
	getOffsiteSnapshots,
	getOffsiteStatus,
	type OffsiteSettingsAnswer,
	type OffsiteSnapshot,
	type OffsiteStatus
} from '$lib/api/offsite';
import { getTasks, type ScheduledTask } from '$lib/api/scheduler';
import type { PageReader } from '$lib/pages/load';
import { later, settle, type Later, type Settled } from '$lib/pages/read';

export type OffsiteRead = {
	/** The settings and the state, which fail together as the section's load. */
	found: Settled<{ answer: OffsiteSettingsAnswer; status: OffsiteStatus }>;
	/** The tasks of the type Off-device copy; [] when they could not be read. */
	tasks: ScheduledTask[];
	/** The snapshots of a copy that is set up; null when none is. */
	snapshots: Later<Settled<OffsiteSnapshot[]>> | null;
};

export type BackupSnapshot = {
	backups: Settled<BackupInfo[]>;
	offsite: OffsiteRead;
};

/** The off-device copy's tasks of the Scheduler. */
export async function offsiteTasks(): Promise<ScheduledTask[]> {
	try {
		return (await getTasks()).filter((task) => task.task_type === 'offsite_copy');
	} catch {
		return [];
	}
}

async function readOffsite(): Promise<OffsiteRead> {
	const found = await settle(
		Promise.all([getOffsiteSettings(), getOffsiteStatus()]).then(([answer, status]) => ({ answer, status }))
	);
	const configured = found.ok && found.value.answer.configured;
	const snapshots = configured
		? later(settle(getOffsiteSnapshots().then((answer) => answer.snapshots)), { ok: false, error: '' } as Settled<
				OffsiteSnapshot[]
			>)
		: null;
	return { found, tasks: found.ok ? await offsiteTasks() : [], snapshots };
}

async function readBackup(): Promise<BackupSnapshot> {
	const [backups, offsite] = await Promise.all([settle(listBackups().then((answer) => answer.backups)), readOffsite()]);
	return { backups, offsite };
}

export const backupReader: PageReader<BackupSnapshot> = {
	key: 'system:backup',
	// The copy's folders are the pools' mount points; its tasks are the Scheduler's.
	areas: ['backup', 'offsite', 'scheduler', 'storage'],
	adminOnly: true,
	read: readBackup
};
