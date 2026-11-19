import { getAppCatalog, getInstalledApps, type AppTemplate, type InstalledApp } from '$lib/api/apps';

export type LoadState = 'loading' | 'loaded' | 'failed';

/** The text of a failed request: the server's message (ApiError) or the error's. */
export const reasonOf = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

/**
 * The catalog and the installed apps of the App Store. They load each on its own: the catalog
 * needs no Docker, and is listed as soon as it came, while the installed apps wait for Docker's
 * answer, or fail as when Docker cannot be reached (503, ADR-0323). A count is shown only from a
 * load that succeeded, and each failed load says so (ADR-0327).
 */
export class AppsData {
	catalog = $state<AppTemplate[]>([]);
	installed = $state<InstalledApp[]>([]);
	catalogState = $state<LoadState>('loading');
	installedState = $state<LoadState>('loading');
	catalogError = $state('');
	installedError = $state('');
	loading = $derived(this.catalogState === 'loading' || this.installedState === 'loading');
	// The apps of the catalog that have an image for this host's platform (ADR-0403).
	availableCount = $derived(this.catalog.filter((app) => app.available).length);

	async loadCatalog() {
		this.catalogState = 'loading';
		try {
			this.catalog = await getAppCatalog();
			this.catalogError = '';
			this.catalogState = 'loaded';
		} catch (e) {
			this.catalogError = reasonOf(e);
			this.catalogState = 'failed';
		}
	}

	async loadInstalled() {
		this.installedState = 'loading';
		try {
			this.installed = await getInstalledApps();
			this.installedError = '';
			this.installedState = 'loaded';
		} catch (e) {
			this.installed = [];
			this.installedError = reasonOf(e);
			this.installedState = 'failed';
		}
	}

	/** The catalog's app of an installed one, if the catalog has it. */
	templateOf(app: InstalledApp): AppTemplate | undefined {
		return this.catalog.find((c) => c.id === app.template_id);
	}
}
