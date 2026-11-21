<script lang="ts">
	// The Storage page: the host's disks, the btrfs filesystems found on them, the pools with the
	// jobs on them, and the dialogs that change them. The page holds the lists (StorageLists, with
	// the jobs of PoolJobs) and which dialog is open, and follows the events socket.
	import { onMount, untrack } from 'svelte';
	import { Plus, RefreshCw } from 'lucide-svelte';
	import { Alert, Button, Skeleton } from '$lib/components/ui';
	import type { BalanceStatus, Disk, DiskRemovalStatus, ImportableFilesystem, Pool } from '$lib/api/storage';
	import { scaleBytes } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { stats, wsConnected } from '$lib/stores/stats';
	import { scrubs } from '$lib/stores/scrubs';
	import { balances } from '$lib/stores/balances';
	import { diskRemovals } from '$lib/stores/diskRemovals';
	import {
		AddDiskDialog,
		BalanceDialog,
		CreatePoolDialog,
		DeletePoolDialog,
		DiskTable,
		FoundFilesystems,
		ImportDialog,
		PoolActionDialog,
		PoolList,
		RemoveDiskDialog,
		SpindownDialog,
		VolumesDialog
	} from '$lib/components/storage';
	import { StorageLists } from '$lib/components/storage/lists.svelte';
	import { PoolVolumes } from '$lib/components/storage/volumes.svelte';
	import type { PoolAction, PoolHandlers } from '$lib/components/storage/pools';
	import { usableForPool } from '$lib/components/storage/pools';

	const lists = new StorageLists(() => dialogOpen);
	const jobs = lists.jobs;
	const volumes = new PoolVolumes();

	// The dialogs: each is open while its target is set.
	let showCreateModal = $state(false);
	// The idle timeout of a disk (ADR-0378), and the server's warning after a Save, shown in the
	// Disks card: the disk's own automatic offline scans could not be turned off for its timeout,
	// or on again; the timeout was set all the same.
	let spindownDisk = $state.raw<Disk | null>(null);
	let spindownWarning = $state('');
	let poolAction = $state.raw<PoolAction | null>(null);
	let poolToDelete = $state.raw<Pool | null>(null);
	let importTarget = $state.raw<ImportableFilesystem | null>(null);
	let balanceTarget = $state.raw<Pool | null>(null);
	// Disk removals (ADR-0411): the pool of the Add Disk or the Remove Disk dialog.
	let addDiskPool = $state.raw<Pool | null>(null);
	let removeDiskPool = $state.raw<Pool | null>(null);

	let dialogOpen = $derived(
		showCreateModal ||
			poolAction !== null ||
			importTarget !== null ||
			poolToDelete !== null ||
			volumes.pool !== null ||
			spindownDisk !== null ||
			balanceTarget !== null ||
			addDiskPool !== null ||
			removeDiskPool !== null
	);

	onMount(async () => {
		await lists.loadData();
		lists.reloadForDevices();
	});

	// A disk attached or removed while the page is open (ADR-0360): each message of the stats
	// stream, and nothing else; what the reload reads is its own business.
	$effect(() => {
		const message = $stats;
		untrack(() => lists.followBlockDevices(message));
	});
	// A dialog that opens drops the reload in flight; once it closes, the reload that waited runs.
	$effect(() => {
		const open = dialogOpen;
		untrack(() => (open ? lists.dropDeviceReload() : lists.reloadForDevices()));
	});
	// The jobs that run are followed over /ws/v1/events (ADR-0367, ADR-0379, ADR-0411).
	$effect(() => {
		const updates = $diskRemovals;
		untrack(() => jobs.followRemovals(updates));
	});
	$effect(() => {
		const updates = $balances;
		untrack(() => jobs.followBalances(updates));
	});
	$effect(() => {
		const updates = $scrubs;
		untrack(() => jobs.followScrubs(updates));
	});
	$effect(() => {
		const connected = $wsConnected;
		untrack(() => jobs.followSocket(connected));
	});

	// ---- How the page writes sizes and names (ADR-0401) ----

	// The units of the sizes the page writes, by powers of 1024 (ADR-0401).
	const SIZE_UNITS = ['storage.size.bytes', 'storage.size.kb', 'storage.size.mb', 'storage.size.gb', 'storage.size.tb'];
	// One decimal, without grouping, as the page has always written its sizes and percentages.
	let oneDecimal = $derived(
		new Intl.NumberFormat($intlLocale, {
			minimumFractionDigits: 1,
			maximumFractionDigits: 1,
			useGrouping: false
		})
	);
	let percentFormat = $derived(
		new Intl.NumberFormat($intlLocale, {
			style: 'percent',
			minimumFractionDigits: 1,
			maximumFractionDigits: 1
		})
	);

	/** A size in the language of the page: the number in its format, with the unit of the catalog. */
	const formatBytes = (bytes: number): string => {
		const { value, power } = scaleBytes(bytes, SIZE_UNITS.length);
		return $t(SIZE_UNITS[power], { size: oneDecimal.format(value) });
	};

	/** A label or a name shown on its own, in the quotes of the page's language. */
	const quoted = (name: string): string => $t('storage.quoted', { name });

	const describeContents = (disk: Disk): string => {
		const parts: string[] = [];
		if (disk.fstype) parts.push(disk.label ? `${disk.fstype} ${quoted(disk.label)}` : disk.fstype);
		if (disk.pttype) {
			// After a filesystem of the whole disk, such as a hybrid ISO image, it continues the list.
			const key = parts.length > 0 ? 'storage.disks.partitionTableAfter' : 'storage.disks.partitionTable';
			parts.push($t(key, { type: disk.pttype.toUpperCase() }));
		}
		const partitionCount = disk.partitions?.length ?? 0;
		if (partitionCount > 0) parts.push($t('storage.disks.partitions', { n: partitionCount }));
		// A sleeping disk is not probed for signatures (ADR-0378): what lsblk does not know is unknown.
		if (parts.length === 0 && disk.power_state === 'standby') return $t('storage.disks.notReadAsleep');
		if (parts.length > 0) return parts.join(', ');
		return $t(disk.has_data ? 'storage.disks.unrecognized' : 'storage.disks.empty');
	};

	/** An idle timeout in minutes in words: Off, "20 minutes", "2 hours". */
	const timeoutLabel = (minutes: number): string => {
		if (minutes === 0) return $t('storage.spindown.off');
		if (minutes % 60 === 0) return $t('storage.spindown.hours', { n: minutes / 60 });
		return $t('storage.spindown.minutes', { n: minutes });
	};

	// btrfs's data profiles, as the server answers a pool's raid_level (ADR-0401).
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
	const profileLabel = (profile: string): string => (PROFILES[profile] ? $t(PROFILES[profile]) : profile);

	// ---- The dialogs ----

	async function openCreateModal() {
		showCreateModal = true;
		// Disk usage may have changed since the page loaded.
		await lists.reloadModalDisks();
	}

	async function poolCreated() {
		showCreateModal = false;
		await lists.loadData(false);
	}

	function openSpindown(disk: Disk) {
		spindownDisk = disk;
		spindownWarning = '';
	}

	async function spindownSaved(warning: string) {
		spindownWarning = warning;
		spindownDisk = null;
		await lists.loadData(false);
	}

	async function poolActionDone() {
		poolAction = null;
		await lists.loadData(false);
	}

	async function imported() {
		importTarget = null;
		await lists.loadData(false);
	}

	async function poolDeleted() {
		poolToDelete = null;
		await lists.loadData(false);
	}

	function diskAdded() {
		addDiskPool = null;
		lists.loadData(false);
	}

	function removalStarted(status: DiskRemovalStatus) {
		const poolName = removeDiskPool?.name;
		removeDiskPool = null;
		if (poolName) jobs.showRemoval(poolName, status);
		// A disk without data leaves at once: the pool's disks changed.
		if (status.status !== 'running') lists.loadData(false);
	}

	function balanceStarted(pool: string, status: BalanceStatus) {
		jobs.balance.show(pool, status);
	}

	// Refresh: the lists again, keeping what is shown until they answer (ADR-0430).
	let refreshing = $state(false);
	async function refresh() {
		refreshing = true;
		try {
			await lists.loadData(false);
		} finally {
			refreshing = false;
		}
	}

	// The disks that a new pool can take, as the Create pool dialog offers them; null when the disks
	// could not be read, which also disables Create pool, with the reason.
	let freeDisks = $derived(lists.disksError ? null : lists.disks.filter(usableForPool).length);

	// What the pools' cards ask for.
	const poolHandlers: PoolHandlers = {
		onscrub: (pool) => jobs.startScrub(pool.name),
		oncancelscrub: (pool) => jobs.cancelScrub(pool.name),
		onbalance: (pool) => (balanceTarget = pool),
		onbalanceaction: (pool, action) => jobs.balanceAction(pool.name, action),
		onadddisk: (pool) => (addDiskPool = pool),
		onremovedisk: (pool) => (removeDiskPool = pool),
		onvolumes: (pool) => volumes.open(pool),
		onmount: (pool) => (poolAction = { kind: 'mount', pool }),
		onunmount: (pool) => (poolAction = { kind: 'unmount', pool }),
		ondelete: (pool) => (poolToDelete = pool)
	};
</script>

<div class="page-container">
	<div class="page-header">
		<h1 class="page-title">{$t('storage.title')}</h1>
		<div class="page-actions">
			<!-- Reads every list again: also the way to try again after a list could not be read. -->
			<Button
				variant="secondary"
				size="icon"
				icon={RefreshCw}
				title={$t('common.refresh')}
				loading={refreshing}
				disabled={lists.loading}
				onclick={refresh}
			>
				<span class="sr-only">{$t('common.refresh')}</span>
			</Button>
			<!-- A pool is made of the disks the page lists: without them, Create pool waits and says why. -->
			<Button
				icon={Plus}
				disabled={!lists.loading && !!lists.disksError}
				disabledReason={lists.disksError ? $t('storage.createBlocked') : ''}
				onclick={openCreateModal}>{$t('storage.createPool')}</Button
			>
		</div>
	</div>

	{#if jobs.error}
		<Alert variant="error">{jobs.error}</Alert>
	{/if}

	{#if lists.loading}
		<!-- The first load (ADR-0430): the pools and the disks' table, swapped for them at once. -->
		<div class="space-y-6" aria-busy="true">
			<Skeleton shape="card" count={2} />
			<Skeleton shape="table" columns={7} count={3} />
		</div>
	{:else}
		<!-- The pools first, the page's subject; then the disks they are made of, and the btrfs
		     filesystems found on them that can become pools. A list that could not be read
		     says so in its card, and a cause that a card above names already is not repeated. -->
		<PoolList
			pools={lists.pools}
			error={lists.poolsError}
			{freeDisks}
			{jobs}
			{formatBytes}
			{percentFormat}
			{profileLabel}
			{...poolHandlers}
		/>

		<DiskTable
			disks={lists.disks}
			error={lists.disksError}
			errorDetail={lists.disksError !== lists.poolsError}
			warning={spindownWarning}
			{formatBytes}
			{quoted}
			{describeContents}
			{timeoutLabel}
			onspindown={openSpindown}
			ondismisswarning={() => (spindownWarning = '')}
		/>

		<FoundFilesystems
			filesystems={lists.importable}
			error={lists.importableError}
			errorDetail={lists.importableError !== lists.disksError && lists.importableError !== lists.poolsError}
			{formatBytes}
			onimport={(fs) => (importTarget = fs)}
		/>
	{/if}
</div>

<CreatePoolDialog
	open={showCreateModal}
	disks={lists.disks}
	disksLoading={lists.disksLoading}
	disksError={lists.disksError}
	{formatBytes}
	{describeContents}
	reloadDisks={() => lists.reloadModalDisks()}
	onclose={() => (showCreateModal = false)}
	oncreated={poolCreated}
/>

<BalanceDialog pool={balanceTarget} onclose={() => (balanceTarget = null)} onstarted={balanceStarted} />

<!-- Add Disk and Remove Disk (ADR-0411) -->
<AddDiskDialog
	pool={addDiskPool}
	{formatBytes}
	{profileLabel}
	onclose={() => (addDiskPool = null)}
	onadded={diskAdded}
/>
<RemoveDiskDialog
	pool={removeDiskPool}
	{formatBytes}
	onclose={() => (removeDiskPool = null)}
	onstarted={removalStarted}
/>

<PoolActionDialog action={poolAction} {quoted} onclose={() => (poolAction = null)} ondone={poolActionDone} />

<SpindownDialog disk={spindownDisk} {timeoutLabel} onclose={() => (spindownDisk = null)} onsaved={spindownSaved} />

<ImportDialog
	target={importTarget}
	pools={lists.pools}
	importable={lists.importable}
	onclose={() => (importTarget = null)}
	onimported={imported}
/>

<DeletePoolDialog
	pool={poolToDelete}
	{formatBytes}
	{quoted}
	onclose={() => (poolToDelete = null)}
	ondeleted={poolDeleted}
/>

<VolumesDialog {volumes} {formatBytes} {percentFormat} />
