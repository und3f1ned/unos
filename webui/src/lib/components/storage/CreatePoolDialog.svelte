<script lang="ts">
	// The Create pool dialog: a name, a RAID profile and the disks of a new pool, which erases them;
	// a disk with data needs the erase confirmed, and the name is typed again. The page loads the
	// disk list again when it opens the dialog, and again here after a disk changed under its path.
	// It lists the disks a pool can take; the others wait behind "N disks not available", each with
	// its reason. The dialog scrolls as a whole, with no box that scrolls inside it, and says what is
	// still missing while Create is disabled.
	import { untrack } from 'svelte';
	import { ChevronDown, HardDrive } from 'lucide-svelte';
	import { Alert, Badge, Button, Checkbox, EmptyState, ErrorState, Input, Select, Sheet, Skeleton } from '$lib/components/ui';
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
	import { messageOf, usableForPool } from './pools';

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
		if (usableForPool(disk)) return null;
		if (disk.is_system) return disk.in_use_reason || $t('storage.create.systemDisk');
		return disk.in_use_reason || $t('storage.create.inUse');
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
	// The disks a pool can take, and the others with their reasons.
	let usable = $derived(disks.filter((d) => unavailableReason(d) === null));
	let unusable = $derived(disks.filter((d) => unavailableReason(d) !== null));
	let showUnusable = $state(false);
	// What Create still waits for, in the order of the form: said under the form while it is
	// disabled.
	let missing = $derived(
		[
			!poolForm.name ? $t('storage.create.needName') : null,
			!hasEnoughDevices ? $t('storage.create.needDisks', { n: requiredDevices }) : null,
			selectedWithData.length > 0 && !poolForm.force ? $t('storage.create.needErase') : null,
			!confirmNameMatches ? $t('storage.create.needConfirm') : null
		].filter((text): text is string => text !== null)
	);
	// A selected disk that a new list of the disks no longer offers (gone, or now in use) leaves the
	// selection: it has no checkbox left to clear, and Create then says what it waits for.
	$effect(() => {
		const list = disks;
		untrack(() => {
			const kept = poolForm.devices.filter((path) =>
				list.some((d) => d.path === path && unavailableReason(d) === null)
			);
			if (kept.length !== poolForm.devices.length) poolForm.devices = kept;
		});
	});
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
		showUnusable = false;
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

<!-- A form of a list of disks and its fields: a Sheet (ADR-0432). -->
<Sheet
	{open}
	title={$t('storage.create.title')}
	size="lg"
	closable={!saving}
	onclose={closeCreateModal}
>
	<!-- The dialog scrolls as a whole, so that the typed confirmation is never hidden. -->
	<form
		id="create-pool-form"
		onsubmit={(e) => { e.preventDefault(); handleCreatePool(); }}
		class="space-y-4"
	>
		<Alert variant="error" size="sm" title={$t('storage.create.eraseTitle')}>
			{$t('storage.create.erase')}
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
				<!-- The disks' place while they are read again (ADR-0430). -->
				<div aria-busy="true"><Skeleton shape="block" class="h-16 w-full" /></div>
			{:else if disksError}
				<ErrorState title={$t('storage.disks.loadError')} message={disksError} onretry={reloadDisks} />
			{:else if disks.length === 0}
				<EmptyState compact icon={HardDrive} title={$t('storage.disks.none')} />
			{:else}
				{#if usable.length === 0}
					<EmptyState compact icon={HardDrive} title={$t('storage.create.noneUsable')} />
				{/if}
				<div class="space-y-2">
					{#each usable as disk}
						<Checkbox checked={poolForm.devices.includes(disk.path)} onchange={() => toggleDevice(disk)}>
							<span class="flex flex-wrap items-center gap-x-2">
								<span class="mono">{disk.path}</span>
								<span class="text-secondary text-sm">{formatBytes(disk.size)}</span>
								{#if disk.model}
									<span class="text-muted text-xs">{disk.model}</span>
								{/if}
								{#if disk.serial}
									<span class="mono-xs text-muted">{disk.serial}</span>
								{/if}
							</span>
							<span class="block text-xs text-secondary">
								{describeContents(disk)}{disk.pool
									? ` · ${$t('storage.create.memberOf', { pool: disk.pool })}`
									: ''}
							</span>
							{#if disk.has_data}
								<Badge variant="warning" class="mt-1">{$t('storage.create.containsData')}</Badge>
							{/if}
						</Checkbox>
					{/each}
				</div>
				{#if unusable.length > 0}
					<!-- The disks a pool cannot take, such as the system disk: folded, each with its reason. -->
					<Button
						variant="ghost"
						size="sm"
						class="mt-2 -ml-2"
						aria-expanded={showUnusable}
						aria-controls="create-pool-unusable"
						onclick={() => (showUnusable = !showUnusable)}
					>
						{$t('storage.create.unusable', { n: unusable.length })}
						<ChevronDown class="w-4 h-4 transition-transform {showUnusable ? 'rotate-180' : ''}" aria-hidden="true" />
					</Button>
					{#if showUnusable}
						<ul id="create-pool-unusable" class="mt-1 space-y-1 text-sm">
							{#each unusable as disk}
								<li class="px-2 text-secondary">
									<span class="mono">{disk.path}</span>
									<span class="text-xs text-muted">{formatBytes(disk.size)}{disk.model ? ` · ${disk.model}` : ''}</span>
									<span class="block text-xs">{$t('storage.create.unavailable', { reason: unavailableReason(disk) ?? '' })}</span>
								</li>
							{/each}
						</ul>
					{/if}
				{/if}
			{/if}
			{#if poolForm.devices.length > 0}
				<p class="text-sm text-secondary mt-2">
					{$t('storage.create.selected', { n: poolForm.devices.length })}
				</p>
			{/if}
			{#if poolForm.devices.length > 0 && !hasEnoughDevices}
				<Alert variant="warning" size="sm" class="mt-2">
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
				<Checkbox bind:checked={poolForm.force} label={$t('storage.create.eraseData')} class="mt-3" />
			</Alert>
		{/if}

		<!-- The name to type is said in the label, and the field starts empty: a placeholder with
		     the name would look filled in. -->
		<Input
			label={poolForm.name ? $t('storage.create.confirmName', { name: poolForm.name }) : $t('storage.create.confirm')}
			bind:value={poolForm.confirm_name}
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

		{#if !canCreate && missing.length > 0}
			<p id="create-pool-missing" class="text-xs text-secondary">
				{$t('storage.create.missing', { list: missing.join(', ') })}
			</p>
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
			aria-describedby={!canCreate && missing.length > 0 ? 'create-pool-missing' : undefined}
			onclick={handleCreatePool}
		>
			{$t('storage.create.submit')}
		</Button>
	{/snippet}
</Sheet>
