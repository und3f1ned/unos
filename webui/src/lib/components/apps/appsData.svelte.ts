import { messageOf } from '$lib/errors';
import { ApiError } from '$lib/api/client';
import { getAppCatalog, getInstalledApps, type AppTemplate, type InstalledApp } from '$lib/api/apps';

export type LoadState = 'loading' | 'loaded' | 'failed';

/** The text of a failed request: the server's message (ApiError) or the error's. */
export const reasonOf = (reason: unknown) => messageOf(reason);

/**
 * The catalog and the installed apps of the App Store. They load each on its own: the catalog
 * needs no Docker, and the page lists it without waiting for the installed apps beyond a moment of
 * its first load (ADR-0430), while those wait for Docker's answer, or fail as when Docker cannot be
 * reached (503, ADR-0323). A count is shown only from a load that succeeded, and each failed load
 * says so (ADR-0327).
 */
export class AppsData {
	catalog = $state<AppTemplate[]>([]);
	installed = $state<InstalledApp[]>([]);
	catalogState = $state<LoadState>('loading');
	installedState = $state<LoadState>('loading');
	catalogError = $state('');
	installedError = $state('');
	// The installed apps failed because Docker did not answer (503, ADR-0323), not for another reason.
	installedDockerDown = $state(false);
	// The last load of each list succeeded: its list and count stay while a refresh runs (ADR-0430).
	catalogKnown = $state(false);
	installedKnown = $state(false);
	loading = $derived(this.catalogState === 'loading' || this.installedState === 'loading');
	// The apps of the catalog that have an image for this host's platform (ADR-0403).
	availableCount = $derived(this.catalog.filter((app) => app.available).length);

	async loadCatalog() {
		this.catalogState = 'loading';
		try {
			this.catalog = await getAppCatalog();
			this.catalogError = '';
			this.catalogState = 'loaded';
			this.catalogKnown = true;
		} catch (e) {
			this.catalogError = reasonOf(e);
			this.catalogState = 'failed';
			this.catalogKnown = false;
		}
	}

	async loadInstalled() {
		this.installedState = 'loading';
		try {
			this.installed = await getInstalledApps();
			this.installedError = '';
			this.installedDockerDown = false;
			this.installedState = 'loaded';
			this.installedKnown = true;
		} catch (e) {
			this.installed = [];
			this.installedError = reasonOf(e);
			this.installedDockerDown = e instanceof ApiError && e.status === 503;
			this.installedState = 'failed';
			this.installedKnown = false;
		}
	}

	/** The catalog's app of an installed one, if the catalog has it. */
	templateOf(app: InstalledApp): AppTemplate | undefined {
		return this.catalog.find((c) => c.id === app.template_id);
	}
}
