// System > Updates' reads (routes/system/updates/+page.ts, ADR-0234, ADR-0443): the version that
// runs, the updater's progress and, where the supervisor can install releases, its history and its
// automatic install. The check for the newest release asks GitHub, the slowest read: the tab shows
// without waiting for it, its place waiting until it answers.
import {
	checkForUpdates,
	getCurrentVersion,
	getUpdateConfig,
	getUpdateHistory,
	getUpdateProgress,
	type CurrentVersion,
	type UpdateCheck,
	type UpdateConfig,
	type UpdateHistoryEntry,
	type UpdateProgress
} from '$lib/api/updates';
import type { PageReader } from '$lib/pages/load';
import { later, settle, type Later, type Settled } from '$lib/pages/read';

/** The in-app updater, as GET /updates/progress and then its history and settings answered. */
export type UpdaterRead = {
	/** null when it could not be read: the tab names the update script then. */
	progress: UpdateProgress | null;
	/** null when they were not read, or could not be. */
	history: UpdateHistoryEntry[] | null;
	config: UpdateConfig | null;
};

export type UpdatesSnapshot = {
	current: Settled<CurrentVersion>;
	updater: UpdaterRead;
	/** The newest stable release. */
	check: Later<Settled<UpdateCheck>>;
};

/** The updater's progress, and its history and settings where it can install releases. */
export async function readUpdater(): Promise<UpdaterRead> {
	const read: UpdaterRead = { progress: null, history: null, config: null };
	try {
		read.progress = await getUpdateProgress();
		if (read.progress.install_unavailable === null) {
			[read.history, read.config] = await Promise.all([getUpdateHistory(), getUpdateConfig()]);
		}
	} catch {
		// What was read stays: without the progress the tab names the script.
	}
	return read;
}

async function readUpdates(): Promise<UpdatesSnapshot> {
	const check = later(settle(checkForUpdates(false)), { ok: false, error: '' });
	const [current, updater] = await Promise.all([settle(getCurrentVersion()), readUpdater()]);
	return { current, updater, check };
}

export const updatesReader: PageReader<UpdatesSnapshot> = {
	key: 'system:updates',
	areas: ['updates'],
	adminOnly: true,
	read: readUpdates
};
