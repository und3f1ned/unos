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
		getImportableFilesystems,
		importFilesystem,
		suggestedPoolName,
		POOL_NAME_PATTERN,
		type DeviceIdentity,
		type Disk,
		type ImportableFilesystem,
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
		Layers,
		HardDriveDownload
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
	import { intlLocale, locale, t } from '$lib/i18n';

	type BadgeVariant = 'success' | 'warning' | 'error' | 'info' | 'neutral' | 'orange';

	let disks: Disk[] = [];
	let pools: Pool[] = [];
	let loading = true;
	let error = '';
	// Why the disk list or the pool list could not be loaded (ADR-0271). A disk list
	// that could not be loaded again is not offered either: it may no longer match the host.
	let disksError = '';
	let poolsError = '';
	// btrfs filesystems on the host's disks that are not pools yet (ADR-0283), with the
	// reason when they could not be searched.
	let importable: ImportableFilesystem[] = [];
	let importableError = '';

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
	// Why a pool's scrub status could not be read: shown instead of the last one (ADR-0272).
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
		// of looking empty, and the other ones are still shown (ADR-0271). The filesystems
		// found on the disks have their own error as well (loadImportable).
		const found = loadImportable();
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
		await found;
		loading = false;
	}

	async function loadImportable() {
		try {
			importable = await getImportableFilesystems();
			importableError = '';
		} catch (e) {
			importable = [];
			importableError = messageOf(e);
		}
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
			// until it stopped (ADR-0272).
			showScrubStatus(poolName, await cancelScrub(poolName));
		} catch (e) {
			error = messageOf(e);
		}
	}

	// How a scrub that did not finish ended, as btrfs names it; another value is shown as it comes.
	const SCRUB_STOPPED: Record<string, string> = {
		aborted: 'storage.scrub.aborted',
		interrupted: 'storage.scrub.interrupted'
	};

	/** The result of a pool's last scrub, as btrfs reports it (ADR-0272). */
	$: scrubResult = (status: ScrubStatus): { text: string; tone: 'success' | 'warning' | 'error' } => {
		if (status.status === 'finished') {
			return status.errors > 0
				? { text: $t('storage.scrub.errors', { n: status.errors }), tone: 'error' }
				: { text: $t('storage.scrub.noErrors'), tone: 'success' };
		}
		const stopped = SCRUB_STOPPED[status.status] ? $t(SCRUB_STOPPED[status.status]) : status.status;
		return {
			text:
				status.errors > 0
					? $t('storage.scrub.stoppedErrors', { stopped, n: status.errors })
					: $t('storage.scrub.stopped', { stopped }),
			tone: status.errors > 0 ? 'error' : 'warning'
		};
	};

	// btrfs prints when a scrub started as C's ctime does, in English and in the time zone of the
	// supervisor's container, which it does not name: "Fri Nov  6 10:00:00 2026".
	const BTRFS_DATE = /^[A-Z][a-z]{2} ([A-Z][a-z]{2}) +(\d{1,2}) (\d{2}):(\d{2}):(\d{2}) (\d{4})$/;
	// i18n-ignore: the names of the months in btrfs's dates
	const BTRFS_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
	// A date and a time as toLocaleString writes them, in UTC: the clock time that btrfs printed.
	$: startedFormat = new Intl.DateTimeFormat($intlLocale, {
		year: 'numeric',
		month: 'numeric',
		day: 'numeric',
		hour: 'numeric',
		minute: 'numeric',
		second: 'numeric',
		// i18n-ignore: a time zone, the same in every language
		timeZone: 'UTC'
	});

	/**
	 * When the last scrub started (ADR-0295): as btrfs prints it on an English page, else the same
	 * date and clock time in the date format of the page's language; a value of another form, or a
	 * date that does not exist, as it comes.
	 */
	$: scrubStarted = (value: string): string => {
		const match = BTRFS_DATE.exec(value);
		const month = match ? BTRFS_MONTHS.indexOf(match[1]) : -1;
		if ($locale === 'en' || !match || month < 0) return value;
		const [, , day, hours, minutes, seconds, year] = match.map(Number);
		const date = new Date(Date.UTC(year, month, day, hours, minutes, seconds));
		// Date.UTC carries what is out of range over to the next field: 30 February is 2 March.
		const exists =
			date.getUTCFullYear() === year &&
			date.getUTCMonth() === month &&
			date.getUTCDate() === day &&
			date.getUTCHours() === hours &&
			date.getUTCMinutes() === minutes &&
			date.getUTCSeconds() === seconds;
		return exists ? startedFormat.format(date) : value;
	};

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

	// The units of the sizes the page writes, by powers of 1024 (ADR-0295).
	const SIZE_UNITS = ['storage.size.bytes', 'storage.size.kb', 'storage.size.mb', 'storage.size.gb', 'storage.size.tb'];
	// One decimal, without grouping, as the page has always written its sizes and percentages.
	$: oneDecimal = new Intl.NumberFormat($intlLocale, {
		minimumFractionDigits: 1,
		maximumFractionDigits: 1,
		useGrouping: false
	});
	$: percentFormat = new Intl.NumberFormat($intlLocale, {
		style: 'percent',
		minimumFractionDigits: 1,
		maximumFractionDigits: 1
	});

	/** A size in the language of the page: the number in its format, with the unit of the catalog. */
	$: formatBytes = (bytes: number): string => {
		let size = bytes;
		let unit = 0;
		while (size >= 1024 && unit < SIZE_UNITS.length - 1) {
			size /= 1024;
			unit++;
		}
		return $t(SIZE_UNITS[unit], { size: oneDecimal.format(size) });
	};

	/** A label or a name shown on its own, in the quotes of the page's language. */
	$: quoted = (name: string): string => $t('storage.quoted', { name });

	/** Where UNOS mounts the pool of this name. */
	function poolMountpoint(name: string): string {
		return `/mnt/pools/${name}`;
	}

	// i18n-ignore: a command, the same in every language
	const RESET_COUNTERS = 'btrfs device stats -z';

	// ---- Disks ----

	/** Why a disk cannot be used for a new pool, or null if it can. */
	$: unavailableReason = (disk: Disk): string | null => {
		if (disk.is_system) return disk.in_use_reason || $t('storage.create.systemDisk');
		if (disk.in_use) return disk.in_use_reason || $t('storage.create.inUse');
		return null;
	};

	$: describeContents = (disk: Disk): string => {
		const parts: string[] = [];
		if (disk.fstype) parts.push(disk.label ? `${disk.fstype} ${quoted(disk.label)}` : disk.fstype);
		if (disk.pttype) {
			// After a filesystem of the whole disk, such as a hybrid ISO image, it continues the list.
			const key = parts.length > 0 ? 'storage.disks.partitionTableAfter' : 'storage.disks.partitionTable';
			parts.push($t(key, { type: disk.pttype.toUpperCase() }));
		}
		const partitionCount = disk.partitions?.length ?? 0;
		if (partitionCount > 0) parts.push($t('storage.disks.partitions', { n: partitionCount }));
		if (parts.length > 0) return parts.join(', ');
		return $t(disk.has_data ? 'storage.disks.unrecognized' : 'storage.disks.empty');
	};

	$: diskStatus = (disk: Disk): { variant: BadgeVariant; label: string } => {
		if (disk.is_system) return { variant: 'error', label: $t('storage.disks.status.system') };
		if (disk.in_use) return { variant: 'warning', label: $t('storage.disks.status.inUse') };
		if (disk.pool) return { variant: 'info', label: $t('storage.disks.status.pool', { pool: disk.pool }) };
		if (disk.has_data) return { variant: 'orange', label: $t('storage.disks.status.hasData') };
		return { variant: 'success', label: $t('storage.disks.status.free') };
	};

	function diskMountpoints(disk: Disk): string {
		if (disk.mountpoints?.length) return disk.mountpoints.join(', ');
		return disk.mountpoint || '-';
	}

	// ---- Pools ----

	// A pool's health as the server answers it, with the variant and the key of its badge.
	const HEALTH_BADGES: Record<string, { variant: BadgeVariant; key: string }> = {
		healthy: { variant: 'success', key: 'storage.health.healthy' },
		degraded: { variant: 'warning', key: 'storage.health.degraded' },
		unmounted: { variant: 'orange', key: 'storage.health.unmounted' },
		error: { variant: 'error', key: 'storage.health.error' },
		unknown: { variant: 'neutral', key: 'storage.health.unknown' }
	};

	/** A pool's health badge; a value without a key is shown as it comes. */
	$: healthBadge = (pool: Pool): { variant: BadgeVariant; label: string } => {
		const badge = HEALTH_BADGES[pool.health];
		return badge
			? { variant: badge.variant, label: $t(badge.key) }
			: { variant: 'error', label: pool.health || $t('storage.health.unknown') };
	};

	// btrfs's data profiles, as the server answers a pool's raid_level (ADR-0295).
	const PROFILES: Record<string, string> = {
		single: 'storage.profile.single',
		dup: 'storage.profile.dup',
		raid0: 'storage.profile.raid0',
		raid1: 'storage.profile.raid1',
		raid1c3: 'storage.profile.raid1c3',
		raid1c4: 'storage.profile.raid1c4',
		raid10: 'storage.profile.raid10',
		raid5: 'storage.profile.raid5',
		raid6: 'storage.profile.raid6'
	};

	/** A pool's RAID profile in the language of the page; one without a key as it comes. */
	$: profileLabel = (profile: string): string => (PROFILES[profile] ? $t(PROFILES[profile]) : profile);

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

	// ---- Import a pool found on the disks (ADR-0283) ----

	// The filesystem the Import dialog is open for.
	let importTarget: ImportableFilesystem | null = null;
	let importName = '';
	let importing = false;
	let importError = '';

	$: importNameValid = POOL_NAME_PATTERN.test(importName);
	$: importNameTaken = pools.some((p) => p.name === importName);
	// The label of another filesystem found on the disks: the server refuses a name that another
	// filesystem has as its label (409), and of two with one label, neither is a pool.
	$: importLabelTaken = importable.some((fs) => fs.uuid !== importTarget?.uuid && fs.label === importName);
	$: importNameError = !importName
		? ''
		: !importNameValid
			? $t('storage.nameRule')
			: importNameTaken
				? $t('storage.import.nameTaken', { name: importName })
				: importLabelTaken
					? $t('storage.import.labelTaken', { name: importName })
					: '';
	$: canImport =
		importTarget !== null && importNameValid && !importNameTaken && !importLabelTaken && !importing;

	$: describeFilesystem = (fs: ImportableFilesystem): string =>
		fs.label === null ? $t('storage.found.noLabel') : $t('storage.found.label', { label: fs.label });

	/** Why the filesystem is not a pool yet: the API mounts a pool under its label. */
	$: labelHint = (fs: ImportableFilesystem): string => {
		if (fs.label_problem === 'duplicate') return $t('storage.found.duplicate', { label: fs.label ?? '' });
		if (fs.label_problem === 'invalid') return $t('storage.found.invalid');
		return $t('storage.found.missing');
	};

	$: missingDevicesText = (fs: ImportableFilesystem): string =>
		$t('storage.found.missingDevices', { n: fs.missing_devices, total: fs.total_devices });

	function openImport(fs: ImportableFilesystem) {
		importTarget = fs;
		importName = suggestedPoolName(fs.label);
		importError = '';
	}

	function closeImport() {
		if (importing) return;
		importTarget = null;
		importError = '';
	}

	async function handleImport() {
		if (!importTarget || !canImport) return;
		importing = true;
		importError = '';
		try {
			await importFilesystem(importTarget.uuid, importName);
			importing = false;
			closeImport();
			await loadData(false);
		} catch (e) {
			// 409, and nothing was changed: the filesystem is in use, misses devices or is on a
			// boot, virtual or system device, the name is taken or still used by shares or apps
			// of an earlier pool, or the filesystem mounts another subvolume than its top level
			importError = messageOf(e);
		} finally {
			importing = false;
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
	$: newVolumeNameError = newVolumeName && !newVolumeNameValid ? $t('storage.nameRule') : '';
	// null: no quota; NaN: one that cannot be sent.
	$: newVolumeQuotaBytes =
		newVolumeQuota.trim() === '' ? null : (quotaBytes(newVolumeQuota.trim()) ?? Number.NaN);
	$: newVolumeQuotaError = Number.isNaN(newVolumeQuotaBytes)
		? $t('storage.volumes.quotaError', { max: MAX_QUOTA_GIB })
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

	// The RAID levels a new pool can have: the keys of their names and of what they do, and the
	// disks they need at least.
	const RAID_LEVELS: { value: RaidLevel; labelKey: string; hintKey: string; minDevices: number }[] = [
		{ value: 'single', labelKey: 'storage.raid.single', hintKey: 'storage.raid.singleHint', minDevices: 1 },
		{ value: 'raid0', labelKey: 'storage.raid.raid0', hintKey: 'storage.raid.raid0Hint', minDevices: 2 },
		{ value: 'raid1', labelKey: 'storage.raid.raid1', hintKey: 'storage.raid.raid1Hint', minDevices: 2 },
		{ value: 'raid1c3', labelKey: 'storage.raid.raid1c3', hintKey: 'storage.raid.raid1c3Hint', minDevices: 3 },
		{ value: 'raid1c4', labelKey: 'storage.raid.raid1c4', hintKey: 'storage.raid.raid1c4Hint', minDevices: 4 },
		{ value: 'raid10', labelKey: 'storage.raid.raid10', hintKey: 'storage.raid.raid10Hint', minDevices: 4 }
	];

	$: raidOptions = RAID_LEVELS.map((level) => ({ value: level.value, label: $t(level.labelKey) }));

	$: selectedRaidLevel = RAID_LEVELS.find((level) => level.value === poolForm.raid_level);
	$: selectedRaidDescription = selectedRaidLevel ? $t(selectedRaidLevel.hintKey) : '';
	$: requiredDevices = selectedRaidLevel?.minDevices ?? 1;
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
		<h1 class="page-title">{$t('storage.title')}</h1>
		<Button icon={Plus} on:click={openCreateModal}>{$t('storage.createPool')}</Button>
	</div>

	{#if error}
		<Alert variant="error">{error}</Alert>
	{/if}

	{#if loading}
		<p class="loading-text">{$t('common.loading')}</p>
	{:else}
		<!-- Disks Section -->
		<Card>
			<h2 class="section-title mb-4">
				<HardDrive class="w-5 h-5" />
				{$t('storage.disks.title')}
			</h2>

			{#if disksError}
				<Alert variant="error" title={$t('storage.disks.loadError')}>{disksError}</Alert>
			{:else if disks.length === 0}
				<p class="text-muted">{$t('storage.disks.none')}</p>
			{:else}
				<div class="overflow-x-auto">
					<table class="table">
						<thead class="table-header">
							<tr>
								<th>{$t('storage.disks.column.device')}</th>
								<th>{$t('storage.disks.column.model')}</th>
								<th>{$t('storage.disks.column.size')}</th>
								<th>{$t('storage.disks.column.type')}</th>
								<th>{$t('storage.disks.column.contents')}</th>
								<th>{$t('storage.disks.column.mountedAt')}</th>
								<th>{$t('storage.disks.column.status')}</th>
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
											{$t(disk.rotational ? 'storage.disks.hdd' : 'storage.disks.ssd')}
										</Badge>
									</td>
									<td>
										<p>{describeContents(disk)}</p>
										{#each disk.partitions ?? [] as part}
											<p class="mono-xs text-muted">
												{part.name} · {formatBytes(part.size)}{part.fstype
													? ` · ${part.fstype}`
													: ''}{part.label ? ` ${quoted(part.label)}` : ''}
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

		<!-- btrfs filesystems on the host's disks that are not pools yet (ADR-0283): without a label,
		     with one that is no pool name, or with one that another filesystem has too, when the
		     pool of that name is the other one or neither. A pool found on the disks is a pool
		     below, and its Mount imports it. -->
		{#if importable.length > 0 || importableError}
			<Card>
				<section aria-labelledby="found-on-the-disks">
					<h2 id="found-on-the-disks" class="section-title mb-4">
						<HardDriveDownload class="w-5 h-5" />
						{$t('storage.found.title')}
					</h2>
					{#if importableError}
						<Alert variant="error" title={$t('storage.found.searchError')}>{importableError}</Alert>
					{:else}
						<p class="text-sm text-secondary mb-4">{$t('storage.found.intro')}</p>
						<ul class="space-y-3">
							{#each importable as fs (fs.uuid)}
								<li class="card-nested flex flex-wrap items-start justify-between gap-3">
									<div class="min-w-0 space-y-1">
										<p class="font-semibold">{describeFilesystem(fs)}</p>
										<p class="mono-xs text-muted break-all">{$t('storage.found.uuid', { uuid: fs.uuid })}</p>
										<p class="text-sm text-secondary">
											<span class="mono-xs">{fs.devices.join(', ')}</span> · {$t('storage.found.usage', {
												size: formatBytes(fs.size),
												used: formatBytes(fs.used)
											})}
										</p>
										<p class="text-xs text-muted">{labelHint(fs)}</p>
										{#if fs.missing_devices > 0}
											<p class="text-sm text-warning">{missingDevicesText(fs)}</p>
										{/if}
									</div>
									<Button
										size="sm"
										icon={HardDriveDownload}
										disabled={fs.missing_devices > 0}
										on:click={() => openImport(fs)}
									>
										{$t('storage.found.import')}<span class="sr-only"
											> {$t('storage.found.importOne', { uuid: fs.uuid })}</span
										>
									</Button>
								</li>
							{/each}
						</ul>
					{/if}
				</section>
			</Card>
		{/if}

		<!-- Pools Section -->
		<Card>
			<h2 class="section-title mb-4">
				<Database class="w-5 h-5" />
				{$t('storage.pools.title')}
			</h2>

			{#if pools.some(p => p.raid_level === 'raid5' || p.raid_level === 'raid6')}
				<Alert variant="warning" title={$t('storage.pools.unsafeTitle')}>{$t('storage.pools.unsafe')}</Alert>
			{/if}

			{#if poolsError}
				<Alert variant="error" title={$t('storage.pools.loadError')}>{poolsError}</Alert>
			{:else if pools.length === 0}
				<p class="text-muted">{$t('storage.pools.none')}</p>
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
										<Badge variant="warning">{$t('storage.pool.notMountedAtBoot')}</Badge>
									{:else if pool.mounted}
										<Badge variant="neutral">{$t('storage.pool.mountedAtBoot')}</Badge>
									{:else if pool.persistent}
										<Badge variant="warning">{$t('storage.pool.inFstab')}</Badge>
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
												{$t('storage.pool.cancelScrub')}
											</Button>
										{:else}
											<Button
												variant="secondary"
												size="sm"
												icon={startingScrub[pool.name] ? Loader2 : RefreshCw}
												disabled={startingScrub[pool.name]}
												on:click={() => handleStartScrub(pool.name)}
											>
												{$t('storage.pool.scrub')}
											</Button>
										{/if}
										<Button
											variant="secondary"
											size="sm"
											icon={Layers}
											on:click={() => openVolumes(pool)}
										>
											{$t('storage.pool.volumes')}
										</Button>
										<Button
											variant="secondary"
											size="sm"
											icon={Unplug}
											on:click={() => openPoolAction('unmount', pool)}
										>
											{$t('storage.pool.unmount')}
										</Button>
									{:else}
										<Button
											variant="primary"
											size="sm"
											icon={Plug}
											on:click={() => openPoolAction('mount', pool)}
										>
											{$t('storage.pool.mount')}
										</Button>
									{/if}
									<Button
										variant="ghost"
										size="icon-sm"
										on:click={() => openDeleteModal(pool)}
									>
										<Trash2 class="w-4 h-4 text-error" />
										<span class="sr-only">{$t('storage.pool.remove', { name: pool.name })}</span>
									</Button>
								</div>
							</div>

							<div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
								<div>
									<span class="text-secondary">{$t('storage.pool.raid')}</span>
									{#if pool.raid_level}
										<span class="ml-2">{profileLabel(pool.raid_level)}</span>
										{#if pool.raid_level === 'raid5' || pool.raid_level === 'raid6'}
											<Badge variant="error">{$t('storage.pool.unsafe')}</Badge>
										{/if}
									{:else}
										<!-- The profile is only read from a mounted filesystem. -->
										<span class="ml-2 text-muted"
											>{$t(pool.mounted ? 'storage.pool.unknown' : 'storage.pool.unknownNotMounted')}</span
										>
									{/if}
								</div>
								<div>
									<span class="text-secondary">{$t('storage.pool.used')}</span>
									{#if pool.mounted}
										<span class="ml-2"
											>{formatBytes(pool.used_size)} / {formatBytes(pool.total_size)}</span
										>
									{:else}
										<span class="ml-2 text-muted">{$t('storage.pool.unknownNotMounted')}</span>
									{/if}
								</div>
								<div>
									<span class="text-secondary">{$t('storage.pool.devices')}</span>
									<span class="ml-2 mono-xs">{pool.devices.join(', ') || '-'}</span>
								</div>
								<div>
									<span class="text-secondary">{$t('storage.pool.mountpoint')}</span>
									<span class="ml-2 mono-xs"
										>{pool.mounted ? pool.mountpoint || '-' : $t('storage.pool.notMounted')}</span
									>
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
								<Alert variant="error" title={$t('storage.pool.missingTitle')} class="mt-3">
									{$t('storage.pool.missing', { n: pool.missing_devices })}
								</Alert>
							{/if}
							{#if pool.forced_read_only}
								<Alert variant="error" title={$t('storage.pool.forcedReadOnlyTitle')} class="mt-3">
									{$t('storage.pool.forcedReadOnly')}
								</Alert>
							{:else if pool.read_only}
								<Alert variant="warning" title={$t('storage.pool.readOnlyTitle')} class="mt-3">
									{$t('storage.pool.readOnly')}
								</Alert>
							{/if}
							{#if pool.device_errors > 0}
								<Alert variant="error" title={$t('storage.pool.deviceErrorsTitle')} class="mt-3">
									{$t('storage.pool.deviceErrors', { n: pool.device_errors })}
									<span class="mono-xs"
										>{RESET_COUNTERS} {pool.mountpoint || poolMountpoint(pool.name)}</span
									>.
								</Alert>
							{/if}
							{#if pool.health === 'unknown'}
								<Alert variant="warning" title={$t('storage.pool.unreadableTitle')} class="mt-3">
									{$t('storage.pool.unreadable')}
								</Alert>
							{/if}

							<!-- Scrub Status: progress while it runs, then the result (ADR-0272) -->
							{#if pool.mounted && scrubErrors[pool.name]}
								<Alert variant="warning" title={$t('storage.scrub.statusError')} class="mt-3">
									{scrubErrors[pool.name]}
								</Alert>
							{:else if pool.mounted && scrubStatus && scrubStatus.status !== 'idle'}
								<div class="mt-3 pt-3 border-t border-border">
									{#if scrubStatus.status === 'running'}
										<div class="space-y-2">
											<div class="flex items-center justify-between text-sm">
												<span class="flex items-center gap-2 text-info">
													<Loader2 class="w-4 h-4 loading-spinner" />
													{$t('storage.scrub.running')}
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
													label={$t('storage.scrub.progress')}
												/>
												<div class="flex justify-between text-xs text-secondary">
													<span
														>{formatBytes(scrubStatus.bytes_scrubbed)} / {formatBytes(
															scrubStatus.total_bytes
														)}</span
													>
													<span>{percentFormat.format(getScrubProgress(scrubStatus) / 100)}</span>
													{#if scrubStatus.rate}
														<span>{$t('storage.scrub.rate', { rate: formatBytes(scrubStatus.rate) })}</span>
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
													{scrubStarted(scrubStatus.last_run)}
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
	title={$t('storage.create.title')}
	size="lg"
	closable={!saving}
	on:close={closeCreateModal}
>
	<!-- Scrolls inside the dialog so the buttons stay reachable on small screens. -->
	<form
		on:submit|preventDefault={handleCreatePool}
		class="space-y-4 max-h-[60vh] overflow-y-auto scrollbar-thin pr-1"
	>
		<Alert variant="error" title={$t('storage.create.eraseTitle')}>
			{$t('storage.create.erase')}
			<strong>{$t('storage.create.eraseAll')}</strong>
		</Alert>

		<Input
			label={$t('storage.create.name')}
			bind:value={poolForm.name}
			placeholder={$t('storage.create.namePlaceholder')}
			required
		/>

		<Select label={$t('storage.create.raidLevel')} bind:value={poolForm.raid_level} options={raidOptions} />
		{#if selectedRaidDescription}
			<p class="text-sm text-secondary -mt-2">{selectedRaidDescription}</p>
		{/if}

		<div>
			<p class="form-label">{$t('storage.create.devices')}</p>
			{#if disksLoading}
				<p class="text-muted text-sm">{$t('storage.create.checkingDisks')}</p>
			{:else if disksError}
				<Alert variant="error" title={$t('storage.disks.loadError')}>{disksError}</Alert>
			{:else if disks.length === 0}
				<p class="text-muted text-sm">{$t('storage.disks.none')}</p>
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
									{describeContents(disk)}{disk.pool
										? ` · ${$t('storage.create.memberOf', { pool: disk.pool })}`
										: ''}
								</p>
								{#if reason}
									<Badge variant="error" class="mt-1">{$t('storage.create.unavailable', { reason })}</Badge>
								{:else if disk.has_data}
									<Badge variant="warning" class="mt-1">{$t('storage.create.containsData')}</Badge>
								{/if}
							</div>
						</label>
					{/each}
				</div>
			{/if}
			{#if poolForm.devices.length > 0}
				<p class="text-sm text-secondary mt-2">
					{$t('storage.create.selected', { n: poolForm.devices.length })}
				</p>
			{/if}
			{#if poolForm.devices.length > 0 && !hasEnoughDevices}
				<Alert variant="warning">
					{$t('storage.create.tooFew', {
						level: poolForm.raid_level.toUpperCase(),
						required: requiredDevices,
						selected: poolForm.devices.length
					})}
				</Alert>
			{/if}
		</div>

		{#if selectedWithData.length > 0}
			<Alert variant="warning" title={$t('storage.create.dataTitle')}>
				<p>{$t('storage.create.notEmpty')}</p>
				<ul class="list-disc ml-5 mt-1 text-sm">
					{#each selectedWithData as disk}
						<li>
							<span class="mono-xs">{disk.path}</span>: {describeContents(disk)}{disk.pool
								? `, ${$t('storage.create.memberOf', { pool: disk.pool })}`
								: ''}
						</li>
					{/each}
				</ul>
				<label class="flex items-center gap-2 mt-3 text-content cursor-pointer">
					<input type="checkbox" bind:checked={poolForm.force} class="form-checkbox" />
					{$t('storage.create.eraseData')}
				</label>
			</Alert>
		{/if}

		<Input
			label={$t('storage.create.confirm')}
			bind:value={poolForm.confirm_name}
			placeholder={poolForm.name || $t('storage.create.confirmPlaceholder')}
			autocomplete="off"
			error={poolForm.confirm_name && !confirmNameMatches ? $t('storage.create.mismatch') : ''}
		/>

		{#if createError}
			<Alert variant="error" title={$t('storage.create.failed')}>
				<p>{createError}</p>
				{#if selectionCleared && !disksLoading}
					<p class="mt-2">
						{$t(disksError ? 'storage.create.cleared' : 'storage.create.reloaded')}
					</p>
				{/if}
			</Alert>
		{/if}
	</form>

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={saving} on:click={closeCreateModal}>
			{$t('common.cancel')}
		</Button>
		<Button
			variant="danger"
			class="flex-1"
			loading={saving}
			disabled={!canCreate}
			on:click={handleCreatePool}
		>
			{$t('storage.create.submit')}
		</Button>
	</svelte:fragment>
</Modal>

<!-- Mount / Unmount Modal -->
<Modal
	open={poolAction !== null}
	title={$t(poolAction?.kind === 'mount' ? 'storage.mount.title' : 'storage.unmount.title')}
	size="md"
	closable={!poolActionBusy}
	on:close={closePoolAction}
>
	{#if poolAction}
		{#if poolAction.kind === 'mount'}
			<p>
				{$t('storage.mount.question')}
				<strong class="text-content">{quoted(poolAction.pool.name)}</strong>
				{$t('storage.mount.at')}
				<span class="mono">{poolMountpoint(poolAction.pool.name)}</span>?
			</p>
			<!-- Also how a pool found on the disks is imported (ADR-0283). -->
			<p class="text-sm text-secondary">{$t('storage.mount.kept')}</p>
		{:else}
			<p>
				{$t('storage.unmount.question')}
				<strong class="text-content">{quoted(poolAction.pool.name)}</strong>?
			</p>
			<Alert variant="warning">
				{$t('storage.unmount.before')}
				<span class="mono-xs">{poolAction.pool.mountpoint || poolMountpoint(poolAction.pool.name)}</span>
				{$t('storage.unmount.after')}
			</Alert>
		{/if}
		{#if poolActionError}
			<Alert
				variant="error"
				title={$t(poolAction.kind === 'mount' ? 'storage.mount.failed' : 'storage.unmount.failed')}
			>
				{poolActionError}
			</Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={poolActionBusy} on:click={closePoolAction}>
			{$t('common.cancel')}
		</Button>
		<Button
			variant={poolAction?.kind === 'unmount' ? 'warning' : 'primary'}
			class="flex-1"
			loading={poolActionBusy}
			on:click={confirmPoolAction}
		>
			{$t(poolAction?.kind === 'mount' ? 'storage.pool.mount' : 'storage.pool.unmount')}
		</Button>
	</svelte:fragment>
</Modal>

<!-- Import Pool Modal (ADR-0283) -->
<Modal
	open={importTarget !== null}
	title={$t('storage.import.title')}
	size="md"
	closable={!importing}
	on:close={closeImport}
>
	{#if importTarget}
		<form on:submit|preventDefault={handleImport} class="space-y-4">
			<p>
				{$t('storage.import.filesystem')}
				<span class="mono-xs break-all">{importTarget.uuid}</span>
				({importTarget.label === null
					? $t('storage.import.noLabel')
					: $t('storage.import.label', { label: importTarget.label })})
				{$t('storage.import.on')}
				<span class="mono-xs">{importTarget.devices.join(', ')}</span>
				{$t('storage.import.asPool')}
			</p>
			<Alert variant="info" title={$t('storage.import.keptTitle')}>
				{$t('storage.import.keptBefore')}
				<span class="mono-xs">{poolMountpoint(importName || $t('storage.import.nameSlot'))}</span>
				{$t('storage.import.keptAfter')}
			</Alert>
			<Input
				label={$t('storage.import.name')}
				bind:value={importName}
				placeholder={$t('storage.import.namePlaceholder')}
				autocomplete="off"
				error={importNameError}
			/>
			{#if importError}
				<Alert variant="error" title={$t('storage.import.failed')}>{importError}</Alert>
			{/if}
		</form>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={importing} on:click={closeImport}>
			{$t('common.cancel')}
		</Button>
		<Button
			class="flex-1"
			icon={HardDriveDownload}
			loading={importing}
			disabled={!canImport}
			on:click={handleImport}
		>
			{$t('storage.found.import')}
		</Button>
	</svelte:fragment>
</Modal>

<!-- Delete Pool Modal -->
<Modal
	open={showDeleteModal}
	title={$t('storage.remove.title')}
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
				<Alert variant="warning" title={$t('storage.remove.warningTitle')}>
					<p>
						{$t('storage.remove.about')}
						<strong class="text-content">{quoted(poolToDelete.name)}</strong>.
					</p>
					<p class="mt-2 text-sm">{$t('storage.remove.kept')}</p>
				</Alert>

				<div class="card-nested text-sm">
					<p class="text-secondary">{$t('storage.remove.details')}</p>
					<ul class="mt-2 space-y-1">
						<li>{$t('storage.remove.size', { size: formatBytes(poolToDelete.total_size) })}</li>
						<li>
							{$t('storage.remove.used', {
								used: poolToDelete.mounted ? formatBytes(poolToDelete.used_size) : $t('storage.pool.unknown')
							})}
						</li>
						<li>{$t('storage.remove.devices', { devices: poolToDelete.devices.join(', ') || '-' })}</li>
						<li>
							{$t('storage.remove.mount', {
								mount: poolToDelete.mounted ? poolToDelete.mountpoint || '-' : $t('storage.pool.notMounted')
							})}
						</li>
					</ul>
				</div>
			</div>
		{:else}
			<div class="space-y-4">
				<Alert variant="error" title={$t('storage.remove.finalTitle')}>
					<p>{$t('storage.remove.typeName')}</p>
					<p class="font-mono text-lg mt-2 bg-surface-overlay px-3 py-2 rounded-lg">
						{poolToDelete.name}
					</p>
				</Alert>

				<Input
					label={$t('storage.remove.confirm')}
					bind:value={deleteConfirmName}
					placeholder={$t('storage.remove.confirmPlaceholder')}
					autocomplete="off"
				/>
			</div>
		{/if}
		{#if deleteError}
			<Alert variant="error" title={$t('storage.remove.failed')}>{deleteError}</Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		{#if poolToDelete}
			{#if deleteStep === 1}
				<Button variant="secondary" class="flex-1" on:click={closeDeleteModal}
					>{$t('common.cancel')}</Button
				>
				<Button variant="danger" class="flex-1" on:click={proceedToStep2}
					>{$t('storage.remove.continue')}</Button
				>
			{:else}
				<Button
					variant="secondary"
					class="flex-1"
					disabled={deleting}
					on:click={() => (deleteStep = 1)}
				>
					{$t('storage.remove.back')}
				</Button>
				<Button
					variant="danger"
					class="flex-1"
					loading={deleting}
					disabled={deleteConfirmName !== poolToDelete.name}
					on:click={handleDeletePool}
				>
					{$t('storage.remove.submit')}
				</Button>
			{/if}
		{/if}
	</svelte:fragment>
</Modal>

<!-- Volumes Modal -->
<Modal
	open={volumesPool !== null}
	title={volumesPool
		? $t('storage.volumes.title', { pool: volumesPool.name })
		: $t('storage.volumes.titleNone')}
	size="lg"
	closable={!volumeBusy}
	on:close={closeVolumes}
>
	{#if volumesPool}
		<div class="space-y-4">
			<!-- btrfs snapshots are not recursive, and scheduled snapshot tasks only take
			     pools: say so before files move into a volume (ADR-0243). -->
			<p class="text-sm text-secondary">
				{$t('storage.volumes.introBefore')}
				<span class="mono-xs">{volumesPool.mountpoint || poolMountpoint(volumesPool.name)}</span>
				{$t('storage.volumes.introAfter')}
			</p>

			{#if volumesReadOnly}
				<Alert variant="warning" title={$t('storage.pool.readOnlyTitle')}>
					{$t('storage.volumes.readOnly')}
					{#if volumesPool.forced_read_only}
						{$t('storage.volumes.forcedReadOnly')}
					{/if}
				</Alert>
			{/if}

			{#if volumesLoading}
				<p class="loading-text">{$t('storage.volumes.loading')}</p>
			{:else if volumesError}
				<Alert variant="error" title={$t('storage.volumes.loadError')}>{volumesError}</Alert>
			{:else if volumes.length === 0}
				<p class="text-muted text-sm">{$t('storage.volumes.none')}</p>
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
								<span class="sr-only">{$t('storage.volumes.deleteOne', { name: volume.name })}</span>
							</Button>
						</li>
					{/each}
				</ul>
			{/if}

			{#if volumeToDelete}
				<Alert variant="error" title={$t('storage.volumes.deleteTitle', { name: volumeToDelete.name })}>
					{$t('storage.volumes.deleteBefore')}
					<span class="mono-xs">{volumeToDelete.path}</span>
					{$t('storage.volumes.deleteAfter')}
				</Alert>
				<Input
					label={$t('storage.volumes.confirm')}
					bind:value={deleteVolumeConfirm}
					placeholder={volumeToDelete.name}
					autocomplete="off"
				/>
				{#if deleteVolumeError}
					<Alert variant="error" title={$t('storage.volumes.deleteFailed')}>{deleteVolumeError}</Alert>
				{/if}
				<div class="flex gap-2">
					<Button
						variant="secondary"
						class="flex-1"
						disabled={deletingVolume}
						on:click={cancelDeleteVolume}
					>
						{$t('common.cancel')}
					</Button>
					<Button
						variant="danger"
						class="flex-1"
						loading={deletingVolume}
						disabled={deleteVolumeConfirm !== volumeToDelete.name}
						on:click={handleDeleteVolume}
					>
						{$t('storage.volumes.delete')}
					</Button>
				</div>
			{:else}
				<form on:submit|preventDefault={handleCreateVolume} class="space-y-2">
					<Input
						label={$t('storage.volumes.name')}
						bind:value={newVolumeName}
						placeholder={$t('storage.volumes.namePlaceholder')}
						autocomplete="off"
						disabled={volumesReadOnly}
						error={newVolumeNameError}
					/>
					<Input
						label={$t('storage.volumes.quota')}
						bind:value={newVolumeQuota}
						placeholder={$t('storage.volumes.quotaPlaceholder')}
						autocomplete="off"
						disabled={volumesReadOnly}
						error={newVolumeQuotaError}
						hint={$t('storage.volumes.quotaHint')}
					/>
					{#if createVolumeError}
						<Alert variant="error" title={$t('storage.volumes.createFailed')}>{createVolumeError}</Alert>
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
						{$t('storage.volumes.create')}
					</Button>
				</form>
			{/if}
		</div>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={volumeBusy} on:click={closeVolumes}>
			{$t('storage.volumes.close')}
		</Button>
	</svelte:fragment>
</Modal>
