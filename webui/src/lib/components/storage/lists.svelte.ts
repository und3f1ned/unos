// The lists of the Storage page: the host's disks, the pools and the btrfs filesystems found on the
// disks, in the order of their loads (ADR-0360), loaded again when a disk is attached or removed,
// and the jobs on the pools, which each load of the lists reads too.
import {
	getDisks,
	getImportableFilesystems,
	getPools,
	type Disk,
	type ImportableFilesystem,
	type Pool
} from '$lib/api/storage';
import type { StorageSnapshot } from '$lib/pages/storage';
import type { WebSocketStats } from '$lib/stores/stats';
import { PoolJobs } from './jobs.svelte';
import { messageOf } from './pools';

export class StorageLists {
	disks: Disk[] = $state.raw([]);
	pools: Pool[] = $state.raw([]);
	loading = $state(true);
	// Why the disk list or the pool list could not be loaded (ADR-0271). A disk list
	// that could not be loaded again is not offered either: it may no longer match the host.
	disksError = $state('');
	poolsError = $state('');
	// btrfs filesystems on the host's disks that are not pools yet (ADR-0283), with the
	// reason when they could not be searched.
	importable: ImportableFilesystem[] = $state.raw([]);
	importableError = $state('');
	// The Create Pool dialog's own load of the disk list (reloadModalDisks).
	disksLoading = $state(false);

	readonly jobs: PoolJobs;
	readonly #dialogOpen: () => boolean;

	/** `dialogOpen`: whether a dialog of the page is open, which keeps its lists as they are. */
	constructor(dialogOpen: () => boolean) {
		this.#dialogOpen = dialogOpen;
		this.jobs = new PoolJobs(
			() => this.pools,
			() => this.loadData(false, false)
		);
	}

	// A disk attached or removed while the page is open (ADR-0360). The stats stream names every
	// block device the kernel has, every 2 seconds (disk_io.disks of /ws/v1/events): the page
	// loads its lists again when those names changed and then stayed the same for one message,
	// so that udev has read the new devices before lsblk reports them. Not while a dialog is
	// open, where the lists stay as the user saw them, but once it closes; nor while the lists
	// load, but once they are loaded.
	#devicesSeen: string | null = null; // the devices of the last message
	#devicesHeld: string | null = null; // the last devices that stayed for a message
	#devicesLoaded: string | null = null; // devicesHeld when the lists were last loaded
	// The reload for the devices in flight: the number of its load, and devicesLoaded before it.
	#deviceReload: { load: number; devices: string | null } | null = null;

	followBlockDevices(message: WebSocketStats | null) {
		const names = message?.disk_io?.disks?.map((disk) => disk.name);
		if (!names) return;
		const devices = [...names].sort().join();
		// The first message names the devices of the lists that the page loads when it opens.
		if (this.#devicesHeld === null || devices === this.#devicesSeen) this.#devicesHeld = devices;
		if (this.#devicesLoaded === null) this.#devicesLoaded = devices;
		this.#devicesSeen = devices;
		this.reloadForDevices();
	}

	reloadForDevices() {
		if (this.#readOnClose && !this.loading && !this.#deviceReload && !this.#dialogOpen()) {
			void this.loadData(false, false);
			return;
		}
		if (this.#devicesHeld === this.#devicesLoaded || this.loading || this.#deviceReload || this.#dialogOpen()) {
			return;
		}
		const devices = this.#devicesLoaded;
		const reload = this.loadData(false, false);
		// loadData took the number of its load before it first waited.
		this.#deviceReload = { load: this.#listsLoad, devices };
		reload.finally(() => {
			this.#deviceReload = null;
			this.reloadForDevices();
		});
	}

	/**
	 * A dialog opened before the reload for the devices answered: its answers are not shown, so that
	 * the dialog's lists stay as the user saw them, and it runs again once the dialog closes, unless
	 * a newer load of the lists has started meanwhile.
	 */
	dropDeviceReload() {
		if (!this.#deviceReload || this.#deviceReload.load !== this.#listsLoad) return;
		this.#listsLoad = ++this.#loadSequence;
		if (this.#disksLoad === this.#deviceReload.load) this.#disksLoad = this.#listsLoad;
		this.#devicesLoaded = this.#deviceReload.devices;
	}

	// The order of the page's loads (ADR-0360): each takes the next number, and its answers are
	// shown only while it is the latest load of their list, so that one that answers late does not
	// overwrite what a newer one, or a dialog opened meanwhile, shows.
	#loadSequence = 0;
	#listsLoad = 0; // the latest load of all the lists (loadData)
	#disksLoad = 0; // the latest load of the disk list (loadData, reloadModalDisks)

	/**
	 * Load the disks, the pools, the filesystems found on the disks and the jobs on the pools. The
	 * error of the page (a scrub that did not start or stop) stays for a reload that the user did
	 * not start, the one for the devices.
	 */
	async loadData(showSpinner = true, startedByUser = true) {
		this.#readOnClose = false;
		const load = ++this.#loadSequence;
		this.#listsLoad = this.#disksLoad = load;
		if (showSpinner) this.loading = true;
		if (startedByUser) this.jobs.error = '';
		if (this.#devicesHeld !== null) this.#devicesLoaded = this.#devicesHeld;
		// Each list on its own: one that cannot be loaded says why in its card instead
		// of looking empty, and the other ones are still shown (ADR-0271). The filesystems
		// found on the disks have their own error as well (loadImportable). The disks come
		// first, alone: the pools and the filesystems found are listed with a read of every
		// disk, which can spin a sleeping one up, so Power is read before (ADR-0378).
		const [diskList] = await Promise.allSettled([getDisks()]);
		if (load === this.#disksLoad) {
			if (diskList.status === 'fulfilled') {
				this.disks = diskList.value;
				this.disksError = '';
			} else {
				// For example 500 "lsblk failed: ..." when the host's disks cannot be listed.
				this.disks = [];
				this.disksError = messageOf(diskList.reason);
			}
			this.disksLoading = false;
		}
		// A newer load, or a dialog opened meanwhile (dropDeviceReload), lists them instead.
		if (load !== this.#listsLoad) return;
		const found = this.#loadImportable(load);
		const [poolList] = await Promise.allSettled([getPools()]);
		if (load === this.#listsLoad) {
			if (poolList.status === 'fulfilled') {
				this.pools = poolList.value;
				this.poolsError = '';
			} else {
				this.pools = [];
				this.poolsError = messageOf(poolList.reason);
			}
			await this.jobs.load(() => load === this.#listsLoad);
		}
		await found;
		if (load === this.#listsLoad) this.loading = false;
	}

	// The #loadSequence after the last snapshot of the page's load was shown.
	#appliedAt = 0;
	// A snapshot came while a dialog was open: the lists are read again once it closes.
	#readOnClose = false;

	/**
	 * The lists and the jobs of one snapshot of the page's reads (its load, $lib/pages/storage), as a
	 * load of the lists that has answered. Not while a dialog is open, whose lists stay as the user
	 * saw them: the lists are read again once it closes. Not after a load of the page's own started
	 * since the last snapshot (a reload after a change, for the devices, the Create Pool dialog's
	 * disks): it read later. The devices stay as the lists' last load or the first stats message
	 * found them (followBlockDevices).
	 */
	apply(snapshot: StorageSnapshot) {
		if (this.#loadSequence !== this.#appliedAt) return;
		if (this.#dialogOpen()) {
			this.#readOnClose = true;
			return;
		}
		const load = ++this.#loadSequence;
		this.#appliedAt = load;
		this.#listsLoad = this.#disksLoad = load;
		const { disks, pools, importable } = snapshot;
		this.disks = disks.ok ? disks.value : [];
		this.disksError = disks.ok ? '' : disks.error;
		this.disksLoading = false;
		this.pools = pools.ok ? pools.value : [];
		this.poolsError = pools.ok ? '' : pools.error;
		this.importable = importable.ok ? importable.value : [];
		this.importableError = importable.ok ? '' : importable.error;
		for (const { pool, status } of snapshot.scrubs) {
			if (status.ok) this.jobs.scrub.show(pool, status.value);
			else this.jobs.scrub.fail(pool, new Error(status.error));
		}
		for (const { pool, status } of snapshot.balances) {
			if (status.ok) this.jobs.balance.show(pool, status.value);
			else this.jobs.balance.fail(pool, new Error(status.error));
		}
		for (const { pool, status } of snapshot.removals) {
			if (status.ok) this.jobs.showRemoval(pool, status.value);
			else this.jobs.removal.fail(pool, new Error(status.error));
		}
		this.loading = false;
	}

	async #loadImportable(load: number) {
		try {
			const found = await getImportableFilesystems();
			if (load !== this.#listsLoad) return;
			this.importable = found;
			this.importableError = '';
		} catch (e) {
			if (load !== this.#listsLoad) return;
			this.importable = [];
			this.importableError = messageOf(e);
		}
	}

	/** The disk list again, alone: the Create Pool dialog checks the disks' use when it opens. */
	async reloadModalDisks() {
		const load = ++this.#loadSequence;
		this.#disksLoad = load;
		this.disksLoading = true;
		this.disksError = '';
		try {
			const list = await getDisks();
			if (load === this.#disksLoad) this.disks = list;
		} catch (e) {
			// Do not offer the old list: it may no longer match the host.
			if (load === this.#disksLoad) {
				this.disks = [];
				this.disksError = messageOf(e);
			}
		} finally {
			if (load === this.#disksLoad) this.disksLoading = false;
		}
	}
}
