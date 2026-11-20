<script lang="ts">
	// The Create Pool dialog: a name, a RAID profile and the disks of a new pool, which erases them;
	// a disk with data needs the erase confirmed, and the name is typed again. The page loads the
	// disk list again when it opens the dialog, and again here after a disk changed under its path.
	import { Alert, Badge, Button, Input, Modal, Select } from '$lib/components/ui';
	import { ApiError } from '$lib/api/client';
	import {
		createPool,
		deviceIdentity,
		isChangedDiskError,
		type DeviceIdentity,
		type Disk,
		type RaidLevel
	} from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { messageOf } from './pools';

	type Props = {
		open: boolean;
		disks: Disk[];
		/** The disk list is being loaded again. */
		disksLoading: boolean;
		/** Why the disk list could not be loaded. */
		disksError: string;
		formatBytes: (bytes: number) => string;
		/** What a disk holds, in words. */
		describeContents: (disk: Disk) => string;
		/** Load the disk list again. */
		reloadDisks: () => Promise<void>;
		onclose: () => void;
		/** The pool was created. */
		oncreated: () => void | Promise<void>;
	};

	let {
		open,
		disks,
		disksLoading,
		disksError,
		formatBytes,
		describeContents,
		reloadDisks,
		onclose,
		oncreated
	}: Props = $props();

	let saving = $state(false);
	let createError = $state('');
	let poolForm = $state({
		name: '',
		devices: [] as string[],
		raid_level: 'single',
		confirm_name: '',
		force: false
	});
	// Each selected disk as the list showed it when the user selected it.
	let selectedIdentities: Record<string, DeviceIdentity> = {};
	// Set after a 409 because a selected path now holds another disk.
	let selectionCleared = $state(false);

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

	/** Why a disk cannot be used for a new pool, or null if it can. */
	const unavailableReason = (disk: Disk): string | null => {
		if (disk.is_system) return disk.in_use_reason || $t('storage.create.systemDisk');
		if (disk.in_use) return disk.in_use_reason || $t('storage.create.inUse');
		return null;
	};

	let raidOptions = $derived(
		RAID_LEVELS.map((level) => ({ value: level.value, label: $t(level.labelKey) }))
	);
	let selectedRaidLevel = $derived(RAID_LEVELS.find((level) => level.value === poolForm.raid_level));
	let selectedRaidDescription = $derived(selectedRaidLevel ? $t(selectedRaidLevel.hintKey) : '');
	let requiredDevices = $derived(selectedRaidLevel?.minDevices ?? 1);
	let hasEnoughDevices = $derived(poolForm.devices.length >= requiredDevices);
	let selectedDisks = $derived(disks.filter((d) => poolForm.devices.includes(d.path)));
	let selectedWithData = $derived(selectedDisks.filter((d) => d.has_data));
	let confirmNameMatches = $derived(poolForm.name !== '' && poolForm.confirm_name === poolForm.name);
	let canCreate = $derived(
		!!poolForm.name &&
			hasEnoughDevices &&
			selectedDisks.every((d) => unavailableReason(d) === null) &&
			confirmNameMatches &&
			(selectedWithData.length === 0 || poolForm.force)
	);

	// The form starts empty each time the dialog opens: it is emptied when the dialog closes, while
	// the dialog shows nothing.
	function reset() {
		poolForm = { name: '', devices: [], raid_level: 'single', confirm_name: '', force: false };
		selectedIdentities = {};
		selectionCleared = false;
		createError = '';
	}

	function closeCreateModal() {
		if (saving) return;
		reset();
		onclose();
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
			reset();
			await oncreated();
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
				await reloadDisks();
			}
		} finally {
			saving = false;
		}
	}
</script>

<Modal
	{open}
	title={$t('storage.create.title')}
	size="lg"
	closable={!saving}
	onclose={closeCreateModal}
>
	<!-- Scrolls inside the dialog so the buttons stay reachable on small screens. -->
	<form
		onsubmit={(e) => { e.preventDefault(); handleCreatePool(); }}
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
								onchange={() => toggleDevice(disk)}
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

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={saving} onclick={closeCreateModal}>
			{$t('common.cancel')}
		</Button>
		<Button
			variant="danger"
			class="flex-1"
			loading={saving}
			disabled={!canCreate}
			onclick={handleCreatePool}
		>
			{$t('storage.create.submit')}
		</Button>
	{/snippet}
</Modal>
