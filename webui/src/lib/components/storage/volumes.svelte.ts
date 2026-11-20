// The Volumes dialog of the Storage page (ADR-0243): the volumes (btrfs subvolumes) of a mounted
// pool, with the forms that create and delete one and the use over time of the one opened. The
// page opens it for a pool and reads `pool` to know that it is open.
import {
	createVolume,
	deleteVolume,
	getVolumeUsage,
	getVolumes,
	quotaBytes,
	VOLUME_NAME_PATTERN,
	type Pool,
	type Volume,
	type VolumeUsagePoint
} from '$lib/api/storage';
import { messageOf } from './pools';

export class PoolVolumes {
	// The mounted pool whose volumes the dialog shows; null while it is closed.
	pool: Pool | null = $state.raw(null);
	list: Volume[] = $state.raw([]);
	loading = $state(false);
	// The pool's list has answered since the dialog opened: a reload keeps it (ADR-0430).
	loaded = $state(false);
	error = $state('');
	newName = $state('');
	// In GiB, as typed; empty for no limit (ADR-0243).
	newQuota = $state('');
	creating = $state(false);
	createError = $state('');
	toDelete: Volume | null = $state.raw(null);
	deleteConfirm = $state('');
	deleting = $state(false);
	deleteError = $state('');

	busy = $derived(this.creating || this.deleting);
	// btrfs cannot create or delete subvolumes on a read-only pool (the API answers 409).
	readOnly = $derived(this.pool?.read_only ?? false);
	newNameValid = $derived(VOLUME_NAME_PATTERN.test(this.newName));
	// null: no quota; NaN: one that cannot be sent.
	newQuotaBytes = $derived(
		this.newQuota.trim() === '' ? null : (quotaBytes(this.newQuota.trim()) ?? Number.NaN)
	);

	// The volume whose use over time the dialog shows, loaded when it is opened.
	usageOf: string | null = $state(null);
	usagePoints: VolumeUsagePoint[] = $state.raw([]);
	usageLoading = $state(false);
	usageError = $state('');
	#usageRequest = 0;

	async toggleUsage(volume: Volume) {
		const pool = this.pool?.name;
		if (!pool) return;
		const request = ++this.#usageRequest;
		if (this.usageOf === volume.name) {
			this.usageOf = null;
			return;
		}
		this.usageOf = volume.name;
		this.usagePoints = [];
		this.usageError = '';
		this.usageLoading = true;
		try {
			const history = await getVolumeUsage(pool, volume.name);
			if (request === this.#usageRequest) this.usagePoints = history.points;
		} catch (e) {
			if (request === this.#usageRequest) this.usageError = messageOf(e);
		} finally {
			if (request === this.#usageRequest) this.usageLoading = false;
		}
	}

	async open(pool: Pool) {
		this.pool = pool;
		this.list = [];
		this.loaded = false;
		this.error = '';
		this.usageOf = null;
		this.#usageRequest++;
		this.newName = '';
		this.newQuota = '';
		this.createError = '';
		this.toDelete = null;
		await this.load();
	}

	close() {
		if (this.busy) return;
		this.pool = null;
		this.toDelete = null;
	}

	// Only the latest request updates the dialog (it may have been reopened for another pool).
	#request = 0;

	async load() {
		const pool = this.pool?.name;
		if (!pool) return;
		const request = ++this.#request;
		this.loading = true;
		try {
			const list = await getVolumes(pool);
			if (request === this.#request) {
				this.list = list;
				this.error = '';
			}
		} catch (e) {
			// e.g. 409 when the pool was unmounted meanwhile
			if (request === this.#request) this.error = messageOf(e);
		} finally {
			if (request === this.#request) {
				this.loading = false;
				this.loaded = true;
			}
		}
	}

	async create() {
		// A quota that cannot be sent is the one the field says is wrong.
		if (!this.pool || !this.newNameValid || Number.isNaN(this.newQuotaBytes) || this.busy || this.readOnly) {
			return;
		}
		this.creating = true;
		this.createError = '';
		try {
			// The pool is part of the URL; the body names the volume and, if one is set, its quota.
			await createVolume(
				this.pool.name,
				this.newQuotaBytes === null
					? { name: this.newName }
					: { name: this.newName, quota: this.newQuotaBytes }
			);
			this.newName = '';
			this.newQuota = '';
			this.creating = false;
			await this.load();
		} catch (e) {
			// 409: not mounted, read-only or not at its top level, the name is taken, or a quota
			// was asked for and quotas are not enabled on the pool (the message says how to
			// enable them); 400: invalid name
			this.createError = messageOf(e);
		} finally {
			this.creating = false;
		}
	}

	askDelete(volume: Volume) {
		if (this.readOnly) return;
		this.toDelete = volume;
		this.deleteConfirm = '';
		this.deleteError = '';
	}

	cancelDelete() {
		if (this.deleting) return;
		this.toDelete = null;
	}

	async delete() {
		if (!this.pool || !this.toDelete || this.deleteConfirm !== this.toDelete.name) return;
		this.deleting = true;
		this.deleteError = '';
		try {
			await deleteVolume(this.pool.name, this.toDelete.name);
			this.deleting = false;
			this.toDelete = null;
			await this.load();
		} catch (e) {
			// 409: in use by containers, shares or exports, or holds snapshots;
			// 503: Docker could not be asked about the containers
			this.deleteError = messageOf(e);
		} finally {
			this.deleting = false;
		}
	}
}
