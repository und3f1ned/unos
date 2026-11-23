import { messageOf } from '$lib/errors';
import { getAppCatalog, getInstalledApps, type AppTemplate, type InstalledApp } from '$lib/api/apps';
import type { AppsSnapshot } from '$lib/pages/apps';
import { settleDocker, type DockerRead } from '$lib/pages/containers';
import { settle, type Later, type Settled } from '$lib/pages/read';

export type LoadState = 'loading' | 'loaded' | 'failed';

/** The text of a failed request: the server's message (ApiError) or the error's. */
export const reasonOf = (reason: unknown) => messageOf(reason);

/**
 * The catalog and the installed apps of the App Store. The page's load reads them
 * ($lib/pages/apps), and they load each on its own after it: the catalog needs no Docker, and the
 * page lists it without waiting for the installed apps beyond a moment (ADR-0430), while those wait
 * for Docker's answer, or fail as when Docker cannot be reached (503, ADR-0323). A count is shown
 * only from a load that succeeded, and each failed load says so (ADR-0327).
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

	// The loads of each list of its own so far (Retry, the reloads after an action or an install's
	// end), and their count when the last snapshot of the page's load was shown: a snapshot comes
	// second to a load of its own that started after it.
	#catalogLoads = 0;
	#catalogAppliedAt = 0;
	#installedLoads = 0;
	#installedAppliedAt = 0;
	// The installed apps of the last snapshot, which may come after it: a newer one's replaces them.
	#installedFrom: Later<DockerRead<InstalledApp[]>> | null = null;

	/**
	 * The lists of one snapshot of the page's load ($lib/pages/apps), as loads that have answered.
	 * The installed apps when they come, if they did not with the catalog; meanwhile the list shown
	 * stays.
	 */
	apply(snapshot: AppsSnapshot) {
		if (this.#catalogLoads === this.#catalogAppliedAt) this.#showCatalog(snapshot.catalog);
		if (this.#installedLoads !== this.#installedAppliedAt) return;
		const part = snapshot.installed;
		this.#installedFrom = part;
		if (part.value !== undefined) {
			this.#showInstalled(part.value);
			return;
		}
		this.installedState = 'loading';
		void part.promise.then((read) => {
			if (this.#installedFrom === part && this.#installedLoads === this.#installedAppliedAt) this.#showInstalled(read);
		});
	}

	/** The next snapshot of the page's load (its Refresh) is newer than the loads of its own so far. */
	expectSnapshot() {
		this.#catalogAppliedAt = this.#catalogLoads;
		this.#installedAppliedAt = this.#installedLoads;
	}

	async loadCatalog() {
		this.#catalogLoads += 1;
		this.catalogState = 'loading';
		this.#showCatalog(await settle(getAppCatalog()));
	}

	async loadInstalled() {
		this.#installedLoads += 1;
		this.installedState = 'loading';
		this.#showInstalled(await settleDocker(getInstalledApps()));
	}

	#showCatalog(read: Settled<AppTemplate[]>) {
		if (read.ok) {
			this.catalog = Array.isArray(read.value) ? read.value : [];
			this.catalogError = '';
			this.catalogState = 'loaded';
			this.catalogKnown = true;
		} else {
			this.catalogError = read.error;
			this.catalogState = 'failed';
			this.catalogKnown = false;
		}
	}

	#showInstalled(read: DockerRead<InstalledApp[]>) {
		if (read.ok) {
			this.installed = Array.isArray(read.value) ? read.value : [];
			this.installedError = '';
			this.installedDockerDown = false;
			this.installedState = 'loaded';
			this.installedKnown = true;
		} else {
			this.installed = [];
			this.installedError = read.error;
			this.installedDockerDown = read.dockerDown;
			this.installedState = 'failed';
			this.installedKnown = false;
		}
	}

	/** The catalog's app of an installed one, if the catalog has it. */
	templateOf(app: InstalledApp): AppTemplate | undefined {
		return this.catalog.find((c) => c.id === app.template_id);
	}
}
