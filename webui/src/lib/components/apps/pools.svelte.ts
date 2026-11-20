import { get } from 'svelte/store';
import { describeApiError, getAppPools, type AppPool } from '$lib/api/apps';
import { t } from '$lib/i18n';

/**
 * The storage pools that an app's data can go to, reloaded every time the install dialog opens,
 * and the one chosen. An installed app already has its folders: none is chosen for it.
 */
export class PoolChoice {
	pools = $state<AppPool[]>([]);
	loading = $state(false);
	error = $state('');
	selectedName = $state('');
	selected = $derived(this.pools.find((pool) => pool.name === this.selectedName) ?? null);
	// Counts the loads asked: an answer that comes after a newer one was asked is dropped.
	#request = 0;

	/** The dialog opened: the pools are read again, or none is listed for an installed app. */
	opened(installed: boolean) {
		this.selectedName = '';
		if (installed) {
			this.#request++;
			this.pools = [];
			this.loading = false;
			this.error = '';
		} else {
			void this.#load();
		}
	}

	async #load() {
		const request = ++this.#request;
		this.loading = true;
		this.error = '';
		try {
			const result = await getAppPools();
			if (request !== this.#request) return;
			this.pools = result;
			// The server sorts pools by name and uses the first one by default.
			if (!this.pools.some((pool) => pool.name === this.selectedName)) {
				this.selectedName = this.pools[0]?.name ?? '';
			}
		} catch (e) {
			if (request !== this.#request) return;
			this.pools = [];
			this.error = describeApiError(e, get(t)('apps.detail.poolsError'));
		} finally {
			if (request === this.#request) this.loading = false;
		}
	}
}
