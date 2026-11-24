// The App Store's reads (routes/apps/+page.ts): the catalog, which needs no Docker, and the installed
// apps, which wait for Docker's answer, at once. Both are the page's point: the load waits for the
// catalog, and for the installed apps a moment more (INSTALLED_GRACE_MS), so that the page is drawn
// once with both when Docker answers in time (ADR-0430's rule, which ADR-0469 keeps); a Docker that
// takes longer does not hold the catalog (ADR-0327): the installed apps are then a part that the
// page shows when it comes.
import {
	getAppCatalog,
	getAppsStandby,
	getInstalledApps,
	type AppTemplate,
	type AppsStandby,
	type InstalledApp
} from '$lib/api/apps';
import { settleDocker, type DockerRead } from './containers';
import type { PageReader } from './load';
import { later, settle, type Later, type Settled } from './read';

/** How long the load waits for the installed apps once the catalog has answered. */
export const INSTALLED_GRACE_MS = 1000;

export type AppsSnapshot = {
	catalog: Settled<AppTemplate[]>;
	installed: Later<DockerRead<InstalledApp[]>>;
	/** Whether the critical mark of the SD standby is offered, and whether UNOS runs from its card (ADR-0498):
	 * the server's RAM, read beside the catalog. */
	standby: Settled<AppsStandby>;
};

// settleDocker never fails: the fallback of the Later part is never used.
const UNREAD: DockerRead<InstalledApp[]> = { ok: false, error: '', dockerDown: false };

async function readApps(): Promise<AppsSnapshot> {
	const installed = later(settleDocker(getInstalledApps()), UNREAD);
	const [catalog, standby] = await Promise.all([settle(getAppCatalog()), settle(getAppsStandby())]);
	await Promise.race([installed.promise, new Promise((resolve) => setTimeout(resolve, INSTALLED_GRACE_MS))]);
	return { catalog, installed, standby };
}

export const appsReader: PageReader<AppsSnapshot> = {
	key: 'apps',
	// An installed app is its containers: a container removed, started or stopped on the Containers
	// page changes the installed apps; whether their data are on a mounted pool is the pools' (storage).
	areas: ['apps', 'containers', 'storage', 'system'],
	adminOnly: true,
	read: readApps
};
