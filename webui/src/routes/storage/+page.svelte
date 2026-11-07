<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import {
		getDisks,
		getPools,
		createPool,
		deletePool,
		mountPool,
		unmountPool,
		startScrub,
		getScrubStatus,
		cancelScrub,
		deviceIdentity,
		isChangedDiskError,
		getVolumes,
		createVolume,
		deleteVolume,
		quotaBytes,
		MAX_QUOTA_GIB,
		VOLUME_NAME_PATTERN,
		type DeviceIdentity,
		type Disk,
		type Pool,
		type RaidLevel,
		type ScrubStatus,
		type Volume
	} from '$lib/api/storage';
	import { ApiError } from '$lib/api/client';
	import {
		HardDrive,
		Database,
		Plus,
		Trash2,
		AlertTriangle,
		RefreshCw,
		CheckCircle,
		XCircle,
		Loader2,
		Plug,
		Unplug,
		Layers
	} from 'lucide-svelte';
	import {
		Button,
		Card,
		Badge,
		Modal,
		Alert,
		ProgressBar,
		Input,
		Select
	} from '$lib/components/ui';

	type BadgeVariant = 'success' | 'warning' | 'error' | 'info' | 'neutral' | 'orange';

	let disks: Disk[] = [];
	let pools: Pool[] = [];
	let loading = true;
	let error = '';
	// Why the disk list or the pool list could not be loaded (ADR-0249). A disk list
	// that could not be loaded again is not offered either: it may no longer match the host.
	let disksError = '';
	let poolsError = '';

	// Create Pool Modal state
	let showCreateModal = false;
	let saving = false;
	let disksLoading = false;
	let createError = '';
	let poolForm = {
		name: '',
		devices: [] as string[],
		raid_level: 'single',
		confirm_name: '',
		force: false
	};
	// Each selected disk as the list showed it when the user selected it.
	let selectedIdentities: Record<string, DeviceIdentity> = {};
	// Set after a 409 because a selected path now holds another disk.
	let selectionCleared = false;

	// Mount / unmount confirmation
	let poolAction: { kind: 'mount' | 'unmount'; pool: Pool } | null = null;
	let poolActionBusy = false;
	let poolActionError = '';

	// Delete Pool Modal state
	let showDeleteModal = false;
	let deleteStep: 1 | 2 = 1;
	let poolToDelete: Pool | null = null;
	let deleteConfirmName = '';
	let deleting = false;
	let deleteError = '';

	// Scrub state
	let scrubStatuses: Record<string, ScrubStatus> = {};
	// Why a pool's scrub status could not be read: shown instead of the last one (ADR-0250).
	let scrubErrors: Record<string, string> = {};
	let scrubPollingIntervals: Record<string, ReturnType<typeof setInterval>> = {};
	let startingScrub: Record<string, boolean> = {};

	onMount(async () => {
		await loadData();
	});

	onDestroy(() => {
		Object.values(scrubPollingIntervals).forEach((interval) => clearInterval(interval));
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function loadData(showSpinner = true) {
		if (showSpinner) loading = true;
		error = '';
		// Each list on its own: one that cannot be loaded says why in its card instead
		// of looking empty, and the other one is still shown (ADR-0249).
		const [diskList, poolList] = await Promise.allSettled([getDisks(), getPools()]);
		if (diskList.status === 'fulfilled') {
			disks = diskList.value;
			disksError = '';
		} else {
			// For example 500 "lsblk failed: ..." when the host's disks cannot be listed.
			disks = [];
			disksError = messageOf(diskList.reason);
		}
		if (poolList.status === 'fulfilled') {
			pools = poolList.value;
			poolsError = '';
		} else {
			pools = [];
			poolsError = messageOf(poolList.reason);
		}
		await loadScrubStatuses();
		loading = false;
	}

	async function loadScrubStatuses() {
		// Scrubbing needs a mounted filesystem.
		for (const pool of pools.filter((p) => p.mounted)) {
			try {
				showScrubStatus(pool.name, await getScrubStatus(pool.name));
			} catch (e) {
				showScrubError(pool.name, e);
			}
		}
	}

	/** Show a pool's scrub as btrfs reports it, and ask again every 2 seconds while it runs. */
	function showScrubStatus(poolName: string, status: ScrubStatus) {
		delete scrubErrors[poolName];
		scrubErrors = scrubErrors;
		scrubStatuses[poolName] = status;
		scrubStatuses = scrubStatuses;
		if (status.status === 'running') {
			startScrubPolling(poolName);
		} else {
			stopScrubPolling(poolName);
		}
	}

	/**
	 * The status could not be read (e.g. btrfs did not answer): say why instead of the last
	 * one, and stop asking. The last one still decides the button, so that a scrub that was
	 * running can be cancelled.
	 */
	function showScrubError(poolName: string, e: unknown) {
		stopScrubPolling(poolName);
		scrubErrors[poolName] = messageOf(e);
		scrubErrors = scrubErrors;
	}

	function startScrubPolling(poolName: string) {
		if (scrubPollingIntervals[poolName]) return;
		scrubPollingIntervals[poolName] = setInterval(async () => {
			try {
				showScrubStatus(poolName, await getScrubStatus(poolName));
			} catch (e) {
				showScrubError(poolName, e);
			}
		}, 2000);
	}

	function stopScrubPolling(poolName: string) {
		if (!scrubPollingIntervals[poolName]) return;
		clearInterval(scrubPollingIntervals[poolName]);
		delete scrubPollingIntervals[poolName];
	}

	async function handleStartScrub(poolName: string) {
		startingScrub[poolName] = true;
		startingScrub = startingScrub;
		error = '';
		try {
			showScrubStatus(poolName, await startScrub(poolName));
		} catch (e) {
			error = messageOf(e);
		} finally {
			startingScrub[poolName] = false;
			startingScrub = startingScrub;
		}
	}

	async function handleCancelScrub(poolName: string) {
		try {
			// btrfs can still report the scrub as running for a moment: then it is followed
			// until it stopped (ADR-0250).
			showScrubStatus(poolName, await cancelScrub(poolName));
		} catch (e) {
			error = messageOf(e);
		}
	}

	/** The result of a pool's last scrub, as btrfs reports it (ADR-0250). */
	function scrubResult(status: ScrubStatus): { text: string; tone: 'success' | 'warning' | 'error' } {
		const found = `${status.errors} error(s)`;
		if (status.status === 'finished') {
			return status.errors > 0
				? { text: `Last scrub: ${found}`, tone: 'error' }
				: { text: 'Last scrub: No errors', tone: 'success' };
		}
		const stopped =
			status.status === 'aborted'
				? 'cancelled before it finished'
				: status.status === 'interrupted'
					? 'interrupted before it finished'
					: status.status;
		return {
			text: `Last scrub: ${stopped}${status.errors > 0 ? `, ${found} found` : ''}`,
			tone: status.errors > 0 ? 'error' : 'warning'
		};
	}

	const scrubResultStyles = {
		success: { icon: CheckCircle, class: 'text-success' },
		warning: { icon: AlertTriangle, class: 'text-warning' },
		error: { icon: XCircle, class: 'text-error' }
	};

	function formatDuration(seconds: number): string {
		const hours = Math.floor(seconds / 3600);
		const minutes = Math.floor((seconds % 3600) / 60);
		const secs = Math.floor(seconds % 60);
		return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
	}

	function getScrubProgress(status: ScrubStatus): number {
		if (!status.bytes_scrubbed || !status.total_bytes || status.total_bytes === 0) {
			return 0;
		}
		return (status.bytes_scrubbed / status.total_bytes) * 100;
	}

	function formatBytes(bytes: number): string {
		const units = ['B', 'KB', 'MB', 'GB', 'TB'];
		let i = 0;
		while (bytes >= 1024 && i < units.length - 1) {
			bytes /= 1024;
			i++;
		}
		return `${bytes.toFixed(1)} ${units[i]}`;
	}

	// ---- Disks ----

	/** Why a disk cannot be used for a new pool, or null if it can. */
	function unavailableReason(disk: Disk): string | null {
		if (disk.is_system) return disk.in_use_reason || 'System disk';
		if (disk.in_use) return disk.in_use_reason || 'In use';
		return null;
	}

	function describeContents(disk: Disk): string {
		const parts: string[] = [];
		if (disk.fstype) parts.push(disk.label ? `${disk.fstype} "${disk.label}"` : disk.fstype);
		if (disk.pttype) parts.push(`${disk.pttype.toUpperCase()} partition table`);
		const partitionCount = disk.partitions?.length ?? 0;
		if (partitionCount > 0) {
			parts.push(`${partitionCount} partition${partitionCount === 1 ? '' : 's'}`);
		}
		if (parts.length > 0) return parts.join(', ');
		return disk.has_data ? 'Unrecognized data' : 'Empty';
	}

	function diskStatus(disk: Disk): { variant: BadgeVariant; label: string } {
		if (disk.is_system) return { variant: 'error', label: 'System' };
		if (disk.in_use) return { variant: 'warning', label: 'In use' };
		if (disk.pool) return { variant: 'info', label: `Pool "${disk.pool}"` };
		if (disk.has_data) return { variant: 'orange', label: 'Has data' };
		return { variant: 'success', label: 'Free' };
	}

	function diskMountpoints(disk: Disk): string {
		if (disk.mountpoints?.length) return disk.mountpoints.join(', ');
		return disk.mountpoint || '-';
	}

	// ---- Pools ----

	const healthBadges: Record<string, { variant: BadgeVariant; label: string }> = {
		healthy: { variant: 'success', label: 'Healthy' },
		degraded: { variant: 'warning', label: 'Degraded' },
		unmounted: { variant: 'orange', label: 'Not mounted' },
		error: { variant: 'error', label: 'Error' },
		unknown: { variant: 'neutral', label: 'Unknown' }
	};

	function healthBadge(pool: Pool): { variant: BadgeVariant; label: string } {
		return healthBadges[pool.health] ?? { variant: 'error', label: pool.health || 'Unknown' };
	}

	function openPoolAction(kind: 'mount' | 'unmount', pool: Pool) {
		poolAction = { kind, pool };
		poolActionError = '';
	}

	function closePoolAction() {
		if (poolActionBusy) return;
		poolAction = null;
		poolActionError = '';
	}

	async function confirmPoolAction() {
		if (!poolAction) return;
		const { kind, pool } = poolAction;
		poolActionBusy = true;
		poolActionError = '';
		try {
			if (kind === 'mount') {
				await mountPool(pool.name);
			} else {
				await unmountPool(pool.name);
			}
			poolActionBusy = false;
			closePoolAction();
			await loadData(false);
		} catch (e) {
			// e.g. 409 "target is busy" when unmounting
			poolActionError = messageOf(e);
		} finally {
			poolActionBusy = false;
		}
	}

	// ---- Create pool ----

	async function openCreateModal() {
		poolForm = { name: '', devices: [], raid_level: 'single', confirm_name: '', force: false };
		selectedIdentities = {};
		selectionCleared = false;
		createError = '';
		showCreateModal = true;
		// Disk usage may have changed since the page loaded.
		await reloadModalDisks();
	}

	async function reloadModalDisks() {
		disksLoading = true;
		disksError = '';
		try {
			disks = await getDisks();
		} catch (e) {
			// Do not offer the old list: it may no longer match the host.
			disks = [];
			disksError = messageOf(e);
		} finally {
			disksLoading = false;
		}
	}

	function closeCreateModal() {
		if (saving) return;
		showCreateModal = false;
	}

	function toggleDevice(disk: Disk) {
		if (poolForm.devices.includes(disk.path)) {
			poolForm.devices = poolForm.devices.filter((d) => d !== disk.path);
			delete selectedIdentities[disk.path];
		} else {
			poolForm.devices = [...poolForm.devices, disk.path];
			selectedIdentities[disk.path] = deviceIdentity(disk);
		}
		// The erase confirmation applies to a specific set of disks.
		poolForm.force = false;
		selectionCleared = false;
	}

	async function handleCreatePool() {
		if (!canCreate) return;
		saving = true;
		createError = '';
		selectionCleared = false;
		try {
			await createPool({
				name: poolForm.name,
				devices: poolForm.devices,
				// The Select only offers RaidLevel values.
				raid_level: poolForm.raid_level as RaidLevel,
				confirm_name: poolForm.confirm_name,
				force: poolForm.force,
				device_identities: poolForm.devices.map((path) => selectedIdentities[path])
			});
			saving = false;
			showCreateModal = false;
			await loadData(false);
		} catch (e) {
			// 409: a disk is in use, holds data or is no longer the disk that
			// was selected; 400: validation failed.
			createError = messageOf(e);
			if (e instanceof ApiError && e.status === 409 && isChangedDiskError(e.message)) {
				// A path now names another disk (disks are renamed when they are
				// reconnected): show the current list and have the user select again.
				poolForm.devices = [];
				poolForm.force = false;
				selectedIdentities = {};
				selectionCleared = true;
				saving = false;
				await reloadModalDisks();
			}
		} finally {
			saving = false;
		}
	}

	// ---- Delete pool ----

	function openDeleteModal(pool: Pool) {
		poolToDelete = pool;
		deleteStep = 1;
		deleteConfirmName = '';
		deleteError = '';
		showDeleteModal = true;
	}

	function closeDeleteModal() {
		if (deleting) return;
		showDeleteModal = false;
		poolToDelete = null;
		deleteStep = 1;
		deleteConfirmName = '';
		deleteError = '';
	}

	function proceedToStep2() {
		deleteStep = 2;
	}

	async function handleDeletePool() {
		if (!poolToDelete || deleteConfirmName !== poolToDelete.name) return;
		deleting = true;
		deleteError = '';
		try {
			await deletePool(poolToDelete.name);
			deleting = false;
			closeDeleteModal();
			await loadData(false);
		} catch (e) {
			deleteError = messageOf(e);
		} finally {
			deleting = false;
		}
	}

	// ---- Volumes ----

	// The mounted pool whose volumes (btrfs subvolumes) the dialog shows.
	let volumesPool: Pool | null = null;
	let volumes: Volume[] = [];
	let volumesLoading = false;
	let volumesError = '';
	let newVolumeName = '';
	// In GiB, as typed; empty for no limit (ADR-0243).
	let newVolumeQuota = '';
	let creatingVolume = false;
	let createVolumeError = '';
	let volumeToDelete: Volume | null = null;
	let deleteVolumeConfirm = '';
	let deletingVolume = false;
	let deleteVolumeError = '';

	$: volumeBusy = creatingVolume || deletingVolume;
	// btrfs cannot create or delete subvolumes on a read-only pool (the API answers 409).
	$: volumesReadOnly = volumesPool?.read_only ?? false;
	$: newVolumeNameValid = VOLUME_NAME_PATTERN.test(newVolumeName);
	$: newVolumeNameError =
		newVolumeName && !newVolumeNameValid
			? "Use letters, numbers, '-' and '_' (up to 63), starting with a letter or number"
			: '';
	// null: no quota; NaN: one that cannot be sent.
	$: newVolumeQuotaBytes =
		newVolumeQuota.trim() === '' ? null : (quotaBytes(newVolumeQuota.trim()) ?? Number.NaN);
	$: newVolumeQuotaError = Number.isNaN(newVolumeQuotaBytes)
		? `Enter a number of GiB greater than 0, such as 10 or 0.5 (at most ${MAX_QUOTA_GIB})`
		: '';

	async function openVolumes(pool: Pool) {
		volumesPool = pool;
		volumes = [];
		newVolumeName = '';
		newVolumeQuota = '';
		createVolumeError = '';
		volumeToDelete = null;
		await loadVolumes();
	}

	function closeVolumes() {
		if (volumeBusy) return;
		volumesPool = null;
		volumeToDelete = null;
	}

	// Only the latest request updates the dialog (it may have been reopened for another pool).
	let volumesRequest = 0;

	async function loadVolumes() {
		const pool = volumesPool?.name;
		if (!pool) return;
		const request = ++volumesRequest;
		volumesLoading = true;
		volumesError = '';
		try {
			const list = await getVolumes(pool);
			if (request === volumesRequest) volumes = list;
		} catch (e) {
			// e.g. 409 when the pool was unmounted meanwhile
			if (request === volumesRequest) volumesError = messageOf(e);
		} finally {
			if (request === volumesRequest) volumesLoading = false;
		}
	}

	async function handleCreateVolume() {
		if (!volumesPool || !newVolumeNameValid || newVolumeQuotaError || volumeBusy || volumesReadOnly) {
			return;
		}
		creatingVolume = true;
		createVolumeError = '';
		try {
			// The pool is part of the URL; the body names the volume and, if one is set, its quota.
			await createVolume(
				volumesPool.name,
				newVolumeQuotaBytes === null
					? { name: newVolumeName }
					: { name: newVolumeName, quota: newVolumeQuotaBytes }
			);
			newVolumeName = '';
			newVolumeQuota = '';
			creatingVolume = false;
			await loadVolumes();
		} catch (e) {
			// 409: not mounted, read-only or not at its top level, the name is taken, or a quota
			// was asked for and quotas are not enabled on the pool (the message says how to
			// enable them); 400: invalid name
			createVolumeError = messageOf(e);
		} finally {
			creatingVolume = false;
		}
	}

	function askDeleteVolume(volume: Volume) {
		if (volumesReadOnly) return;
		volumeToDelete = volume;
		deleteVolumeConfirm = '';
		deleteVolumeError = '';
	}

	function cancelDeleteVolume() {
		if (deletingVolume) return;
		volumeToDelete = null;
	}

	async function handleDeleteVolume() {
		if (!volumesPool || !volumeToDelete || deleteVolumeConfirm !== volumeToDelete.name) return;
		deletingVolume = true;
		deleteVolumeError = '';
		try {
			await deleteVolume(volumesPool.name, volumeToDelete.name);
			deletingVolume = false;
			volumeToDelete = null;
			await loadVolumes();
		} catch (e) {
			// 409: in use by containers, shares or exports, or holds snapshots;
			// 503: Docker could not be asked about the containers
			deleteVolumeError = messageOf(e);
		} finally {
			deletingVolume = false;
		}
	}

	const raidOptions = [
		{ value: 'single', label: 'Single (No RAID) — 1+ disk' },
		{ value: 'raid0', label: 'RAID 0 (Striping) — 2+ disks' },
		{ value: 'raid1', label: 'RAID 1 (Mirror, 2 copies) — 2+ disks' },
		{ value: 'raid1c3', label: 'RAID 1C3 (Mirror, 3 copies) — 3+ disks' },
		{ value: 'raid1c4', label: 'RAID 1C4 (Mirror, 4 copies) — 4+ disks' },
		{ value: 'raid10', label: 'RAID 10 (Mirrored stripes) — 4+ disks' }
	];

	const raidDescriptions: Record<string, string> = {
		single: 'No redundancy. All space available. A single disk failure loses all data.',
		raid0: 'Striped across disks for performance. No redundancy. Any disk failure loses all data.',
		raid1: 'Mirrored to 2 copies. Survives 1 disk failure. 50% space efficiency.',
		raid1c3: 'Mirrored to 3 copies. Survives 2 disk failures. 33% space efficiency.',
		raid1c4: 'Mirrored to 4 copies. Survives 3 disk failures. 25% space efficiency.',
		raid10: 'Striped mirrors. Survives 1 failure per mirror pair. 50% space efficiency.'
	};

	const minDevices: Record<string, number> = {
		single: 1, raid0: 2, raid1: 2, raid1c3: 3, raid1c4: 4, raid10: 4
	};

	$: selectedRaidDescription = raidDescriptions[poolForm.raid_level] || '';
	$: requiredDevices = minDevices[poolForm.raid_level] || 1;
	$: hasEnoughDevices = poolForm.devices.length >= requiredDevices;
	$: selectedDisks = disks.filter((d) => poolForm.devices.includes(d.path));
	$: selectedWithData = selectedDisks.filter((d) => d.has_data);
	$: confirmNameMatches = poolForm.name !== '' && poolForm.confirm_name === poolForm.name;
	$: canCreate =
		!!poolForm.name &&
		hasEnoughDevices &&
		selectedDisks.every((d) => unavailableReason(d) === null) &&
		confirmNameMatches &&
		(selectedWithData.length === 0 || poolForm.force);
</script>

<div class="page-container">
	<div class="page-header">
		<h1 class="page-title">Storage</h1>
		<Button icon={Plus} on:click={openCreateModal}>Create Pool</Button>
	</div>

	{#if error}
		<Alert variant="error">{error}</Alert>
	{/if}

	{#if loading}
		<p class="loading-text">Loading...</p>
	{:else}
		<!-- Disks Section -->
		<Card>
			<h2 class="section-title mb-4">
				<HardDrive class="w-5 h-5" />
				Physical Disks
			</h2>

			{#if disksError}
				<Alert variant="error" title="Could not load the disks">{disksError}</Alert>
			{:else if disks.length === 0}
				<p class="text-muted">No disks found</p>
			{:else}
				<div class="overflow-x-auto">
					<table class="table">
						<thead class="table-header">
							<tr>
								<th>Device</th>
								<th>Model</th>
								<th>Size</th>
								<th>Type</th>
								<th>Contents</th>
								<th>Mounted at</th>
								<th>Status</th>
							</tr>
						</thead>
						<tbody>
							{#each disks as disk}
								{@const status = diskStatus(disk)}
								<tr class="table-row align-top">
									<td class="mono">{disk.path}</td>
									<td>
										<p>{disk.model || '-'}</p>
										{#if disk.serial}
											<p class="mono-xs text-muted">{disk.serial}</p>
										{/if}
									</td>
									<td>{formatBytes(disk.size)}</td>
									<td>
										<Badge variant={disk.rotational ? 'warning' : 'success'}>
											{disk.rotational ? 'HDD' : 'SSD'}
										</Badge>
									</td>
									<td>
										<p>{describeContents(disk)}</p>
										{#each disk.partitions ?? [] as part}
											<p class="mono-xs text-muted">
												{part.name} · {formatBytes(part.size)}{part.fstype
													? ` · ${part.fstype}`
													: ''}{part.label ? ` "${part.label}"` : ''}
											</p>
										{/each}
									</td>
									<td class="mono-xs">{diskMountpoints(disk)}</td>
									<td>
										<Badge variant={status.variant}>{status.label}</Badge>
										{#if disk.in_use_reason}
											<p class="text-xs text-muted mt-1">{disk.in_use_reason}</p>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</Card>

		<!-- Pools Section -->
		<Card>
			<h2 class="section-title mb-4">
				<Database class="w-5 h-5" />
				Storage Pools
			</h2>

			{#if pools.some(p => p.raid_level === 'raid5' || p.raid_level === 'raid6')}
				<Alert variant="warning" title="Unsafe RAID Configuration Detected">
					One or more pools use BTRFS RAID5/RAID6, which has a known write-hole bug
					that can cause data corruption on power failure. Consider backing up and
					recreating the pool with a safe RAID level (RAID1, RAID1C3, or RAID1C4).
				</Alert>
			{/if}

			{#if poolsError}
				<Alert variant="error" title="Could not load the pools">{poolsError}</Alert>
			{:else if pools.length === 0}
				<p class="text-muted">No pools configured</p>
			{:else}
				<div class="space-y-4">
					{#each pools as pool}
						{@const scrubStatus = scrubStatuses[pool.name]}
						{@const health = healthBadge(pool)}
						<div class="card-nested">
							<div class="flex items-center justify-between mb-2 gap-2">
								<div class="flex flex-wrap items-center gap-2">
									<h3 class="font-semibold">{pool.name}</h3>
									<Badge variant={health.variant}>{health.label}</Badge>
									{#if pool.mounted && !pool.persistent}
										<Badge variant="warning">Not mounted at boot</Badge>
									{:else if pool.mounted}
										<Badge variant="neutral">Mounted at boot</Badge>
									{:else if pool.persistent}
										<Badge variant="warning">In fstab, but not mounted</Badge>
									{/if}
								</div>
								<div class="flex items-center gap-2">
									{#if pool.mounted}
										{#if scrubStatus?.status === 'running'}
											<Button
												variant="warning"
												size="sm"
												icon={XCircle}
												on:click={() => handleCancelScrub(pool.name)}
											>
												Cancel Scrub
											</Button>
										{:else}
											<Button
												variant="secondary"
												size="sm"
												icon={startingScrub[pool.name] ? Loader2 : RefreshCw}
												disabled={startingScrub[pool.name]}
												on:click={() => handleStartScrub(pool.name)}
											>
												Scrub
											</Button>
										{/if}
										<Button
											variant="secondary"
											size="sm"
											icon={Layers}
											on:click={() => openVolumes(pool)}
										>
											Volumes
										</Button>
										<Button
											variant="secondary"
											size="sm"
											icon={Unplug}
											on:click={() => openPoolAction('unmount', pool)}
										>
											Unmount
										</Button>
									{:else}
										<Button
											variant="primary"
											size="sm"
											icon={Plug}
											on:click={() => openPoolAction('mount', pool)}
										>
											Mount
										</Button>
									{/if}
									<Button
										variant="ghost"
										size="icon-sm"
										on:click={() => openDeleteModal(pool)}
									>
										<Trash2 class="w-4 h-4 text-error" />
										<span class="sr-only">Remove pool {pool.name}</span>
									</Button>
								</div>
							</div>

							<div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
								<div>
									<span class="text-secondary">RAID:</span>
									{#if pool.raid_level}
										<span class="ml-2">{pool.raid_level}</span>
										{#if pool.raid_level === 'raid5' || pool.raid_level === 'raid6'}
											<Badge variant="error">Unsafe</Badge>
										{/if}
									{:else}
										<!-- The profile is only read from a mounted filesystem. -->
										<span class="ml-2 text-muted"
											>{pool.mounted ? 'unknown' : 'unknown (not mounted)'}</span
										>
									{/if}
								</div>
								<div>
									<span class="text-secondary">Used:</span>
									{#if pool.mounted}
										<span class="ml-2"
											>{formatBytes(pool.used_size)} / {formatBytes(pool.total_size)}</span
										>
									{:else}
										<span class="ml-2 text-muted">unknown (not mounted)</span>
									{/if}
								</div>
								<div>
									<span class="text-secondary">Devices:</span>
									<span class="ml-2 mono-xs">{pool.devices.join(', ') || '-'}</span>
								</div>
								<div>
									<span class="text-secondary">Mount:</span>
									<span class="ml-2 mono-xs">{pool.mounted ? pool.mountpoint || '-' : 'Not mounted'}</span>
								</div>
							</div>

							{#if pool.mounted && pool.total_size > 0}
								<ProgressBar
									value={(pool.used_size / pool.total_size) * 100}
									variant="info"
									class="mt-3"
								/>
							{/if}

							{#if pool.missing_devices > 0}
								<Alert variant="error" title="Pool is missing devices" class="mt-3">
									{pool.missing_devices} device{pool.missing_devices === 1 ? ' is' : 's are'} missing.
									Reconnect or replace {pool.missing_devices === 1 ? 'it' : 'them'} before the
									remaining redundancy is lost.
								</Alert>
							{/if}
							{#if pool.forced_read_only}
								<Alert variant="error" title="The filesystem is read-only" class="mt-3">
									Writes to this pool fail. btrfs stops writing after a serious error, such as an
									aborted transaction; check the kernel log (dmesg) for the cause.
								</Alert>
							{:else if pool.read_only}
								<Alert variant="warning" title="Mounted read-only" class="mt-3">
									Shares and apps cannot write to this pool until it is mounted read-write again.
								</Alert>
							{/if}
							{#if pool.device_errors > 0}
								<Alert variant="error" title="Device errors reported" class="mt-3">
									btrfs counted {pool.device_errors} read/write/checksum error{pool.device_errors === 1
										? ''
										: 's'} on this pool's devices. Check the disks' SMART health and run a scrub.
									The counters keep their values until they are reset with
									<span class="mono-xs"
										>btrfs device stats -z {pool.mountpoint || `/mnt/pools/${pool.name}`}</span
									>.
								</Alert>
							{/if}
							{#if pool.health === 'unknown'}
								<Alert variant="warning" title="Pool state could not be read" class="mt-3">
									UNOS could not read the host mount table or this pool's btrfs error counters, so its
									health is unknown. Check the system logs.
								</Alert>
							{/if}

							<!-- Scrub Status: progress while it runs, then the result (ADR-0250) -->
							{#if pool.mounted && scrubErrors[pool.name]}
								<Alert variant="warning" title="Could not read the scrub status" class="mt-3">
									{scrubErrors[pool.name]}
								</Alert>
							{:else if pool.mounted && scrubStatus && scrubStatus.status !== 'idle'}
								<div class="mt-3 pt-3 border-t border-border">
									{#if scrubStatus.status === 'running'}
										<div class="space-y-2">
											<div class="flex items-center justify-between text-sm">
												<span class="flex items-center gap-2 text-info">
													<Loader2 class="w-4 h-4 loading-spinner" />
													Scrub in progress
												</span>
												<span class="text-secondary">
													{#if scrubStatus.duration}
														{formatDuration(scrubStatus.duration)}
													{/if}
												</span>
											</div>
											{#if scrubStatus.total_bytes && scrubStatus.bytes_scrubbed}
												<ProgressBar
													value={getScrubProgress(scrubStatus)}
													variant="info"
													label="Scrub progress"
												/>
												<div class="flex justify-between text-xs text-secondary">
													<span
														>{formatBytes(scrubStatus.bytes_scrubbed)} / {formatBytes(
															scrubStatus.total_bytes
														)}</span
													>
													<span>{getScrubProgress(scrubStatus).toFixed(1)}%</span>
													{#if scrubStatus.rate}
														<span>{formatBytes(scrubStatus.rate)}/s</span>
													{/if}
												</div>
											{/if}
										</div>
									{:else}
										{@const result = scrubResult(scrubStatus)}
										{@const style = scrubResultStyles[result.tone]}
										<div class="flex items-center justify-between text-sm">
											<span class="flex items-center gap-2 {style.class}">
												<svelte:component this={style.icon} class="w-4 h-4" />
												{result.text}
											</span>
											<span class="text-xs text-muted">
												{#if scrubStatus.last_run}
													{scrubStatus.last_run}
												{/if}
											</span>
										</div>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</Card>
	{/if}
</div>

<!-- Create Pool Modal -->
<Modal
	open={showCreateModal}
	title="Create Storage Pool"
	size="lg"
	closable={!saving}
	on:close={closeCreateModal}
>
	<!-- Scrolls inside the dialog so the buttons stay reachable on small screens. -->
	<form
		on:submit|preventDefault={handleCreatePool}
		class="space-y-4 max-h-[60vh] overflow-y-auto scrollbar-thin pr-1"
	>
		<Alert variant="error" title="This erases the selected disks">
			Creating a pool formats the selected disks with btrfs.
			<strong>ALL DATA on the selected disks will be erased.</strong>
		</Alert>

		<Input label="Pool Name" bind:value={poolForm.name} placeholder="data" required />

		<Select label="RAID Level" bind:value={poolForm.raid_level} options={raidOptions} />
		{#if selectedRaidDescription}
			<p class="text-sm text-secondary -mt-2">{selectedRaidDescription}</p>
		{/if}

		<div>
			<p class="form-label">Select Devices</p>
			{#if disksLoading}
				<p class="text-muted text-sm">Checking disks...</p>
			{:else if disksError}
				<Alert variant="error" title="Could not load the disks">{disksError}</Alert>
			{:else if disks.length === 0}
				<p class="text-muted text-sm">No disks found</p>
			{:else}
				<div class="space-y-2 max-h-48 overflow-y-auto scrollbar-thin">
					{#each disks as disk}
						{@const reason = unavailableReason(disk)}
						<label
							class="flex items-start gap-3 p-2 bg-surface-muted rounded-lg transition-colors {reason
								? 'opacity-60 cursor-not-allowed'
								: 'cursor-pointer hover:bg-surface-overlay'}"
						>
							<input
								type="checkbox"
								checked={poolForm.devices.includes(disk.path)}
								disabled={reason !== null}
								on:change={() => toggleDevice(disk)}
								class="form-checkbox mt-1"
							/>
							<div class="flex-1 min-w-0">
								<div class="flex flex-wrap items-center gap-x-2">
									<span class="mono">{disk.path}</span>
									<span class="text-secondary text-sm">{formatBytes(disk.size)}</span>
									{#if disk.model}
										<span class="text-muted text-xs">{disk.model}</span>
									{/if}
									{#if disk.serial}
										<span class="mono-xs text-muted">{disk.serial}</span>
									{/if}
								</div>
								<p class="text-xs text-secondary">
									{describeContents(disk)}{disk.pool ? ` · member of pool "${disk.pool}"` : ''}
								</p>
								{#if reason}
									<Badge variant="error" class="mt-1">Not available: {reason}</Badge>
								{:else if disk.has_data}
									<Badge variant="warning" class="mt-1">Contains data</Badge>
								{/if}
							</div>
						</label>
					{/each}
				</div>
			{/if}
			{#if poolForm.devices.length > 0}
				<p class="text-sm text-secondary mt-2">
					Selected: {poolForm.devices.length} device(s)
				</p>
			{/if}
			{#if poolForm.devices.length > 0 && !hasEnoughDevices}
				<Alert variant="warning">
					{poolForm.raid_level.toUpperCase()} requires at least {requiredDevices} devices.
					You have selected {poolForm.devices.length}.
				</Alert>
			{/if}
		</div>

		{#if selectedWithData.length > 0}
			<Alert variant="warning" title="Existing data will be destroyed">
				<p>These disks are not empty:</p>
				<ul class="list-disc ml-5 mt-1 text-sm">
					{#each selectedWithData as disk}
						<li>
							<span class="mono-xs">{disk.path}</span>: {describeContents(disk)}{disk.pool
								? `, member of pool "${disk.pool}"`
								: ''}
						</li>
					{/each}
				</ul>
				<label class="flex items-center gap-2 mt-3 text-content cursor-pointer">
					<input type="checkbox" bind:checked={poolForm.force} class="form-checkbox" />
					Erase the existing data on these disks
				</label>
			</Alert>
		{/if}

		<Input
			label="Type the pool name to confirm"
			bind:value={poolForm.confirm_name}
			placeholder={poolForm.name || 'pool name'}
			autocomplete="off"
			error={poolForm.confirm_name && !confirmNameMatches ? 'Does not match the pool name' : ''}
		/>

		{#if createError}
			<Alert variant="error" title="Pool was not created">
				<p>{createError}</p>
				{#if selectionCleared && !disksLoading}
					<p class="mt-2">
						{#if disksError}
							The selection has been cleared. Close this dialog and open it again to reload the
							disks.
						{:else}
							The disk list has been reloaded and the selection cleared. Check the disks and select
							them again.
						{/if}
					</p>
				{/if}
			</Alert>
		{/if}
	</form>

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={saving} on:click={closeCreateModal}>
			Cancel
		</Button>
		<Button
			variant="danger"
			class="flex-1"
			loading={saving}
			disabled={!canCreate}
			on:click={handleCreatePool}
		>
			Erase Disks &amp; Create Pool
		</Button>
	</svelte:fragment>
</Modal>

<!-- Mount / Unmount Modal -->
<Modal
	open={poolAction !== null}
	title={poolAction?.kind === 'mount' ? 'Mount Pool' : 'Unmount Pool'}
	size="md"
	closable={!poolActionBusy}
	on:close={closePoolAction}
>
	{#if poolAction}
		{#if poolAction.kind === 'mount'}
			<p>
				Mount <strong class="text-content">"{poolAction.pool.name}"</strong> at
				<span class="mono">/mnt/pools/{poolAction.pool.name}</span>?
			</p>
			<p class="text-sm text-secondary">
				UNOS also adds an /etc/fstab entry so the pool is mounted again after a reboot.
			</p>
		{:else}
			<p>
				Unmount <strong class="text-content">"{poolAction.pool.name}"</strong>?
			</p>
			<Alert variant="warning">
				Shares, apps and scheduled tasks that use
				<span class="mono-xs">{poolAction.pool.mountpoint || `/mnt/pools/${poolAction.pool.name}`}</span>
				lose access until the pool is mounted again. The /etc/fstab entry is removed, so the pool
				is not mounted at boot. The data on the disks is not touched.
			</Alert>
		{/if}
		{#if poolActionError}
			<Alert
				variant="error"
				title={poolAction.kind === 'mount' ? 'Mount failed' : 'Unmount failed'}
			>
				{poolActionError}
			</Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={poolActionBusy} on:click={closePoolAction}>
			Cancel
		</Button>
		<Button
			variant={poolAction?.kind === 'unmount' ? 'warning' : 'primary'}
			class="flex-1"
			loading={poolActionBusy}
			on:click={confirmPoolAction}
		>
			{poolAction?.kind === 'mount' ? 'Mount' : 'Unmount'}
		</Button>
	</svelte:fragment>
</Modal>

<!-- Delete Pool Modal -->
<Modal
	open={showDeleteModal}
	title="Remove Pool"
	size="md"
	danger
	closable={!deleting}
	on:close={closeDeleteModal}
>
	<svelte:fragment slot="title-icon">
		<AlertTriangle class="w-6 h-6" />
	</svelte:fragment>

	{#if poolToDelete}
		{#if deleteStep === 1}
			<div class="space-y-4">
				<Alert variant="warning" title="Remove pool from UNOS">
					<p>
						You are about to remove the storage pool <strong class="text-content"
							>"{poolToDelete.name}"</strong
						>.
					</p>
					<p class="mt-2 text-sm">
						UNOS unmounts it and removes its /etc/fstab entry. The disks are not wiped:
						the btrfs filesystem and its data stay on them, and the pool can be mounted
						again later. Shares and apps that use it stop working.
					</p>
				</Alert>

				<div class="card-nested text-sm">
					<p class="text-secondary">Pool details:</p>
					<ul class="mt-2 space-y-1">
						<li>Size: {formatBytes(poolToDelete.total_size)}</li>
						<li>Used: {poolToDelete.mounted ? formatBytes(poolToDelete.used_size) : 'unknown'}</li>
						<li>Devices: {poolToDelete.devices.join(', ') || '-'}</li>
						<li>Mount: {poolToDelete.mounted ? poolToDelete.mountpoint || '-' : 'Not mounted'}</li>
					</ul>
				</div>
			</div>
		{:else}
			<div class="space-y-4">
				<Alert variant="error" title="Final Confirmation">
					<p>To confirm, please type the pool name:</p>
					<p class="font-mono text-lg mt-2 bg-surface-overlay px-3 py-2 rounded-lg">
						{poolToDelete.name}
					</p>
				</Alert>

				<Input
					label="Type pool name to confirm"
					bind:value={deleteConfirmName}
					placeholder="Enter pool name"
					autocomplete="off"
				/>
			</div>
		{/if}
		{#if deleteError}
			<Alert variant="error" title="Pool was not removed">{deleteError}</Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		{#if poolToDelete}
			{#if deleteStep === 1}
				<Button variant="secondary" class="flex-1" on:click={closeDeleteModal}
					>Cancel</Button
				>
				<Button variant="danger" class="flex-1" on:click={proceedToStep2}>Continue</Button>
			{:else}
				<Button
					variant="secondary"
					class="flex-1"
					disabled={deleting}
					on:click={() => (deleteStep = 1)}
				>
					Back
				</Button>
				<Button
					variant="danger"
					class="flex-1"
					loading={deleting}
					disabled={deleteConfirmName !== poolToDelete.name}
					on:click={handleDeletePool}
				>
					Remove Pool
				</Button>
			{/if}
		{/if}
	</svelte:fragment>
</Modal>

<!-- Volumes Modal -->
<Modal
	open={volumesPool !== null}
	title={volumesPool ? `Volumes on ${volumesPool.name}` : 'Volumes'}
	size="lg"
	closable={!volumeBusy}
	on:close={closeVolumes}
>
	{#if volumesPool}
		<div class="space-y-4">
			<!-- btrfs snapshots are not recursive, and scheduled snapshot tasks only take
			     pools: say so before files move into a volume (ADR-0243). -->
			<p class="text-sm text-secondary">
				A volume is a btrfs subvolume in
				<span class="mono-xs">{volumesPool.mountpoint || `/mnt/pools/${volumesPool.name}`}</span>
				that can be shared on its own. Snapshots of the pool do not include the files in its
				volumes: take snapshots of each volume as well, on the Snapshots page. Scheduled snapshot
				tasks take pools only.
			</p>

			{#if volumesReadOnly}
				<Alert variant="warning" title="Mounted read-only">
					Volumes cannot be created or deleted while the pool is mounted read-only.
					{#if volumesPool.forced_read_only}
						btrfs made it read-only after an error; check the kernel log (dmesg).
					{/if}
				</Alert>
			{/if}

			{#if volumesLoading}
				<p class="loading-text">Loading volumes...</p>
			{:else if volumesError}
				<Alert variant="error" title="Could not load the volumes">{volumesError}</Alert>
			{:else if volumes.length === 0}
				<p class="text-muted text-sm">No volumes yet</p>
			{:else}
				<ul class="space-y-2">
					{#each volumes as volume (volume.name)}
						<li class="card-nested flex items-center justify-between gap-2">
							<div class="min-w-0">
								<p class="font-semibold">{volume.name}</p>
								<p class="mono-xs text-muted break-all">{volume.path}</p>
							</div>
							<Button
								variant="ghost"
								size="icon-sm"
								disabled={volumeBusy || volumesReadOnly}
								on:click={() => askDeleteVolume(volume)}
							>
								<Trash2 class="w-4 h-4 text-error" />
								<span class="sr-only">Delete volume {volume.name}</span>
							</Button>
						</li>
					{/each}
				</ul>
			{/if}

			{#if volumeToDelete}
				<Alert variant="error" title={`Delete volume "${volumeToDelete.name}"?`}>
					All files in <span class="mono-xs">{volumeToDelete.path}</span> are deleted with it,
					including those of stopped apps. This cannot be undone. While a running app or
					container, an SMB share or an NFS export uses the volume, it is not deleted.
				</Alert>
				<Input
					label="Type the volume name to confirm"
					bind:value={deleteVolumeConfirm}
					placeholder={volumeToDelete.name}
					autocomplete="off"
				/>
				{#if deleteVolumeError}
					<Alert variant="error" title="Volume was not deleted">{deleteVolumeError}</Alert>
				{/if}
				<div class="flex gap-2">
					<Button
						variant="secondary"
						class="flex-1"
						disabled={deletingVolume}
						on:click={cancelDeleteVolume}
					>
						Cancel
					</Button>
					<Button
						variant="danger"
						class="flex-1"
						loading={deletingVolume}
						disabled={deleteVolumeConfirm !== volumeToDelete.name}
						on:click={handleDeleteVolume}
					>
						Delete Volume
					</Button>
				</div>
			{:else}
				<form on:submit|preventDefault={handleCreateVolume} class="space-y-2">
					<Input
						label="New volume name"
						bind:value={newVolumeName}
						placeholder="documents"
						autocomplete="off"
						disabled={volumesReadOnly}
						error={newVolumeNameError}
					/>
					<Input
						label="Quota in GiB (optional)"
						bind:value={newVolumeQuota}
						placeholder="No limit"
						autocomplete="off"
						disabled={volumesReadOnly}
						error={newVolumeQuotaError}
						hint="Empty for no limit. A quota needs quotas enabled on the pool."
					/>
					{#if createVolumeError}
						<Alert variant="error" title="Volume was not created">{createVolumeError}</Alert>
					{/if}
					<Button
						type="submit"
						icon={Plus}
						loading={creatingVolume}
						disabled={!newVolumeNameValid ||
							!!newVolumeQuotaError ||
							volumeBusy ||
							volumesReadOnly}
					>
						Create Volume
					</Button>
				</form>
			{/if}
		</div>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={volumeBusy} on:click={closeVolumes}>
			Close
		</Button>
	</svelte:fragment>
</Modal>
