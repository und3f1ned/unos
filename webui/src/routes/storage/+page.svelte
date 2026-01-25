<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import {
		getDisks,
		getPools,
		createPool,
		deletePool,
		startScrub,
		getScrubStatus,
		cancelScrub,
		type Disk,
		type Pool,
		type ScrubStatus
	} from '$lib/api/storage';
	import {
		HardDrive,
		Database,
		Plus,
		Trash2,
		AlertTriangle,
		RefreshCw,
		CheckCircle,
		XCircle,
		Loader2
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

	let disks: Disk[] = [];
	let pools: Pool[] = [];
	let loading = true;
	let error = '';

	// Create Pool Modal state
	let showCreateModal = false;
	let saving = false;
	let poolForm = {
		name: '',
		devices: [] as string[],
		raid_level: 'single'
	};

	// Delete Pool Modal state
	let showDeleteModal = false;
	let deleteStep: 1 | 2 = 1;
	let poolToDelete: Pool | null = null;
	let deleteConfirmName = '';
	let deleting = false;

	// Scrub state
	let scrubStatuses: Record<string, ScrubStatus> = {};
	let scrubPollingIntervals: Record<string, ReturnType<typeof setInterval>> = {};
	let startingScrub: Record<string, boolean> = {};

	onMount(async () => {
		await loadData();
	});

	onDestroy(() => {
		Object.values(scrubPollingIntervals).forEach((interval) => clearInterval(interval));
	});

	async function loadData() {
		loading = true;
		error = '';
		try {
			[disks, pools] = await Promise.all([getDisks(), getPools()]);
			await loadScrubStatuses();
		} catch (e: any) {
			error = e.message;
		} finally {
			loading = false;
		}
	}

	async function loadScrubStatuses() {
		for (const pool of pools) {
			try {
				const status = await getScrubStatus(pool.name);
				scrubStatuses[pool.name] = status;
				scrubStatuses = scrubStatuses;
				if (status.status === 'running') {
					startScrubPolling(pool.name);
				}
			} catch {
				// Ignore errors
			}
		}
	}

	function startScrubPolling(poolName: string) {
		if (scrubPollingIntervals[poolName]) return;
		scrubPollingIntervals[poolName] = setInterval(async () => {
			try {
				const status = await getScrubStatus(poolName);
				scrubStatuses[poolName] = status;
				scrubStatuses = scrubStatuses;
				if (status.status !== 'running') {
					clearInterval(scrubPollingIntervals[poolName]);
					delete scrubPollingIntervals[poolName];
				}
			} catch {
				clearInterval(scrubPollingIntervals[poolName]);
				delete scrubPollingIntervals[poolName];
			}
		}, 2000);
	}

	async function handleStartScrub(poolName: string) {
		startingScrub[poolName] = true;
		startingScrub = startingScrub;
		error = '';
		try {
			const status = await startScrub(poolName);
			scrubStatuses[poolName] = status;
			scrubStatuses = scrubStatuses;
			startScrubPolling(poolName);
		} catch (e: any) {
			error = e.message;
		} finally {
			startingScrub[poolName] = false;
			startingScrub = startingScrub;
		}
	}

	async function handleCancelScrub(poolName: string) {
		try {
			const status = await cancelScrub(poolName);
			scrubStatuses[poolName] = status;
			scrubStatuses = scrubStatuses;
			if (scrubPollingIntervals[poolName]) {
				clearInterval(scrubPollingIntervals[poolName]);
				delete scrubPollingIntervals[poolName];
			}
		} catch (e: any) {
			error = e.message;
		}
	}

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

	function getAvailableDisks(): Disk[] {
		const usedDevices = new Set(pools.flatMap((p) => p.devices));
		return disks.filter((d) => !d.mountpoint && !usedDevices.has(d.path));
	}

	function openCreateModal() {
		poolForm = { name: '', devices: [], raid_level: 'single' };
		showCreateModal = true;
	}

	function toggleDevice(path: string) {
		if (poolForm.devices.includes(path)) {
			poolForm.devices = poolForm.devices.filter((d) => d !== path);
		} else {
			poolForm.devices = [...poolForm.devices, path];
		}
	}

	async function handleCreatePool() {
		if (!poolForm.name || poolForm.devices.length === 0) {
			error = 'Please provide a name and select at least one device';
			return;
		}
		saving = true;
		error = '';
		try {
			await createPool(poolForm);
			showCreateModal = false;
			await loadData();
		} catch (e: any) {
			error = e.message;
		} finally {
			saving = false;
		}
	}

	function openDeleteModal(pool: Pool) {
		poolToDelete = pool;
		deleteStep = 1;
		deleteConfirmName = '';
		showDeleteModal = true;
	}

	function closeDeleteModal() {
		showDeleteModal = false;
		poolToDelete = null;
		deleteStep = 1;
		deleteConfirmName = '';
	}

	function proceedToStep2() {
		deleteStep = 2;
	}

	async function handleDeletePool() {
		if (!poolToDelete || deleteConfirmName !== poolToDelete.name) return;
		deleting = true;
		error = '';
		try {
			await deletePool(poolToDelete.name);
			closeDeleteModal();
			await loadData();
		} catch (e: any) {
			error = e.message;
		} finally {
			deleting = false;
		}
	}

	const raidOptions = [
		{ value: 'single', label: 'Single (No RAID)' },
		{ value: 'raid0', label: 'RAID 0 (Striping)' },
		{ value: 'raid1', label: 'RAID 1 (Mirroring)' },
		{ value: 'raid5', label: 'RAID 5 (requires 3+ disks)' },
		{ value: 'raid6', label: 'RAID 6 (requires 4+ disks)' },
		{ value: 'raid10', label: 'RAID 10 (requires 4+ disks)' }
	];
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

			{#if disks.length === 0}
				<p class="text-muted">No disks found</p>
			{:else}
				<div class="overflow-x-auto">
					<table class="table">
						<thead class="table-header">
							<tr>
								<th>Device</th>
								<th>Model</th>
								<th>Serial</th>
								<th>Size</th>
								<th>Type</th>
								<th>Mount</th>
							</tr>
						</thead>
						<tbody>
							{#each disks as disk}
								<tr class="table-row">
									<td class="mono">{disk.path}</td>
									<td>{disk.model || '-'}</td>
									<td class="mono-xs">{disk.serial || '-'}</td>
									<td>{formatBytes(disk.size)}</td>
									<td>
										<Badge variant={disk.rotational ? 'warning' : 'success'}>
											{disk.rotational ? 'HDD' : 'SSD'}
										</Badge>
									</td>
									<td class="mono-xs">{disk.mountpoint || '-'}</td>
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

			{#if pools.length === 0}
				<p class="text-muted">No pools configured</p>
			{:else}
				<div class="space-y-4">
					{#each pools as pool}
						{@const scrubStatus = scrubStatuses[pool.name]}
						<div class="card-nested">
							<div class="flex items-center justify-between mb-2">
								<h3 class="font-semibold">{pool.name}</h3>
								<div class="flex items-center gap-2">
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
										variant="ghost"
										size="icon-sm"
										on:click={() => openDeleteModal(pool)}
									>
										<Trash2 class="w-4 h-4 text-error" />
									</Button>
								</div>
							</div>

							<div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
								<div>
									<span class="text-secondary">RAID:</span>
									<span class="ml-2">{pool.raid_level || 'single'}</span>
								</div>
								<div>
									<span class="text-secondary">Used:</span>
									<span class="ml-2"
										>{formatBytes(pool.used_size)} / {formatBytes(pool.total_size)}</span
									>
								</div>
								<div>
									<span class="text-secondary">Devices:</span>
									<span class="ml-2">{pool.devices.length}</span>
								</div>
								<div>
									<span class="text-secondary">Mount:</span>
									<span class="ml-2 mono-xs">{pool.mountpoint || '-'}</span>
								</div>
							</div>

							{#if pool.total_size > 0}
								<ProgressBar
									value={(pool.used_size / pool.total_size) * 100}
									variant="info"
									class="mt-3"
								/>
							{/if}

							<!-- Scrub Status -->
							{#if scrubStatus}
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
									{:else if scrubStatus.status === 'finished' || scrubStatus.status === 'aborted'}
										<div class="flex items-center justify-between text-sm">
											<span
												class="flex items-center gap-2 {scrubStatus.errors > 0
													? 'text-error'
													: 'text-success'}"
											>
												{#if scrubStatus.errors > 0}
													<XCircle class="w-4 h-4" />
													Last scrub: {scrubStatus.errors} error(s)
												{:else}
													<CheckCircle class="w-4 h-4" />
													Last scrub: No errors
												{/if}
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
	bind:open={showCreateModal}
	title="Create Storage Pool"
	size="lg"
	on:close={() => (showCreateModal = false)}
>
	<form on:submit|preventDefault={handleCreatePool} class="space-y-4">
		<Input label="Pool Name" bind:value={poolForm.name} placeholder="data" required />

		<Select label="RAID Level" bind:value={poolForm.raid_level} options={raidOptions} />

		<div>
			<label class="form-label">Select Devices</label>
			{#if getAvailableDisks().length === 0}
				<p class="text-muted text-sm">No available disks (all mounted or in use)</p>
			{:else}
				<div class="space-y-2 max-h-48 overflow-y-auto scrollbar-thin">
					{#each getAvailableDisks() as disk}
						<label
							class="flex items-center gap-3 p-2 bg-surface-muted rounded-lg cursor-pointer hover:bg-surface-overlay transition-colors"
						>
							<input
								type="checkbox"
								checked={poolForm.devices.includes(disk.path)}
								on:change={() => toggleDevice(disk.path)}
								class="form-checkbox"
							/>
							<div class="flex-1">
								<span class="mono">{disk.path}</span>
								<span class="text-secondary ml-2">{formatBytes(disk.size)}</span>
								{#if disk.model}
									<span class="text-muted ml-2 text-xs">{disk.model}</span>
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
		</div>
	</form>

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" on:click={() => (showCreateModal = false)}>
			Cancel
		</Button>
		<Button
			variant="primary"
			class="flex-1"
			loading={saving}
			disabled={poolForm.devices.length === 0}
			on:click={handleCreatePool}
		>
			Create Pool
		</Button>
	</svelte:fragment>
</Modal>

<!-- Delete Pool Modal -->
<Modal
	bind:open={showDeleteModal}
	title="Delete Pool"
	size="md"
	danger
	on:close={closeDeleteModal}
>
	<svelte:fragment slot="title-icon">
		<AlertTriangle class="w-6 h-6" />
	</svelte:fragment>

	{#if poolToDelete}
		{#if deleteStep === 1}
			<div class="space-y-4">
				<Alert variant="error" title="Warning!">
					<p>
						You are about to delete the storage pool <strong class="text-content"
							>"{poolToDelete.name}"</strong
						>.
					</p>
					<p class="mt-2 text-sm">
						This will unmount the pool. Data on the disks will remain intact but will no
						longer be accessible through UNOS.
					</p>
				</Alert>

				<div class="card-nested text-sm">
					<p class="text-secondary">Pool details:</p>
					<ul class="mt-2 space-y-1">
						<li>Size: {formatBytes(poolToDelete.total_size)}</li>
						<li>Used: {formatBytes(poolToDelete.used_size)}</li>
						<li>Devices: {poolToDelete.devices.length}</li>
						<li>Mount: {poolToDelete.mountpoint || 'Not mounted'}</li>
					</ul>
				</div>
			</div>

			<svelte:fragment slot="footer">
				<Button variant="secondary" class="flex-1" on:click={closeDeleteModal}
					>Cancel</Button
				>
				<Button variant="danger" class="flex-1" on:click={proceedToStep2}>Continue</Button>
			</svelte:fragment>
		{:else}
			<div class="space-y-4">
				<Alert variant="error" title="Final Confirmation">
					<p>To confirm deletion, please type the pool name:</p>
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

			<svelte:fragment slot="footer">
				<Button variant="secondary" class="flex-1" on:click={() => (deleteStep = 1)}>
					Back
				</Button>
				<Button
					variant="danger"
					class="flex-1"
					loading={deleting}
					disabled={deleteConfirmName !== poolToDelete.name}
					on:click={handleDeletePool}
				>
					Delete Pool
				</Button>
			</svelte:fragment>
		{/if}
	{/if}
</Modal>
