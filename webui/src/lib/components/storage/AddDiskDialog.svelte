<script lang="ts">
	// Add a free disk to a mounted pool (ADR-0411): the server offers the whole disks that hold
	// neither the system, nor a btrfs filesystem, nor a mount; the add erases the disk, once its path
	// is typed again and, for a disk with data, the erase is confirmed.
	import { createEventDispatcher } from 'svelte';
	import { Alert, Badge, Button, Input, Modal } from '$lib/components/ui';
	import { addPoolDisk, getPoolDevices, type DiskCandidate, type Pool } from '$lib/api/storage';
	import { t } from '$lib/i18n';

	/** The pool to add a disk to; the dialog is open while it is set. */
	export let pool: Pool | null = null;
	/** A size in the language of the page. */
	export let formatBytes: (bytes: number) => string;
	/** A profile in the language of the page. */
	export let profileLabel: (profile: string) => string;

	const dispatch = createEventDispatcher<{ close: void; added: Pool }>();

	let loadedFor: Pool | null = null;
	let loading = false;
	let loadError = '';
	let candidates: DiskCandidate[] = [];
	let profiles: string[] = [];
	let selectedPath = '';
	let confirm = '';
	let erase = false;
	let busy = false;
	let error = '';

	$: if (pool !== loadedFor) load(pool);
	$: selected = candidates.find((disk) => disk.path === selectedPath) ?? null;
	// Each disk's erase is confirmed on its own.
	$: forgetErase(selectedPath);
	$: confirmed = selected !== null && confirm.trim() === selected.path;
	$: canAdd = confirmed && (!selected?.has_data || erase) && !busy;

	async function load(target: Pool | null) {
		loadedFor = target;
		candidates = [];
		profiles = [];
		selectedPath = '';
		confirm = '';
		erase = false;
		error = '';
		loadError = '';
		if (!target) return;
		loading = true;
		try {
			const found = await getPoolDevices(target.name);
			if (loadedFor !== target) return;
			candidates = found.candidates;
			profiles = found.profiles;
		} catch (e) {
			if (loadedFor === target) loadError = e instanceof Error ? e.message : String(e);
		} finally {
			if (loadedFor === target) loading = false;
		}
	}

	function forgetErase(_path: string) {
		erase = false;
	}

	function close() {
		if (!busy) dispatch('close');
	}

	async function add() {
		if (!pool || !selected || !canAdd) return;
		busy = true;
		error = '';
		try {
			const grown = await addPoolDisk(pool.name, selected, confirm.trim(), erase);
			busy = false;
			dispatch('added', grown);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			busy = false;
		}
	}
</script>

<Modal
	open={pool !== null}
	title={$t('storage.addDisk.title', { name: pool?.name ?? '' })}
	size="md"
	closable={!busy}
	on:close={close}
>
	{#if pool}
		<p class="text-sm text-secondary">
			{$t('storage.addDisk.intro', { profiles: profiles.map(profileLabel).join(', ') || '-' })}
		</p>
		{#if loading}
			<p class="text-muted text-sm">{$t('storage.addDisk.loading')}</p>
		{:else if loadError}
			<Alert variant="error" title={$t('storage.addDisk.loadError')}>{loadError}</Alert>
		{:else if candidates.length === 0}
			<Alert variant="info" title={$t('storage.addDisk.noneTitle')}>{$t('storage.addDisk.none')}</Alert>
		{:else}
			<fieldset class="space-y-2 min-w-0">
				<legend class="form-label">{$t('storage.addDisk.disk')}</legend>
				{#each candidates as disk (disk.path)}
					<label
						class="flex items-start gap-3 p-2 bg-surface-muted rounded-lg cursor-pointer hover:bg-surface-overlay"
					>
						<input
							type="radio"
							name="add-disk"
							class="mt-1 border-border bg-surface-overlay text-brand focus:ring-brand-ring"
							value={disk.path}
							bind:group={selectedPath}
							disabled={busy}
						/>
						<span class="flex-1 min-w-0">
							<span class="flex flex-wrap items-center gap-x-2">
								<span class="mono break-all">{disk.path}</span>
								<span class="text-secondary text-sm">{formatBytes(disk.size)}</span>
								{#if disk.model}<span class="text-muted text-xs">{disk.model}</span>{/if}
								{#if disk.serial}<span class="mono-xs text-muted">{disk.serial}</span>{/if}
							</span>
							{#if disk.has_data}
								<Badge variant="warning" class="mt-1">{$t('storage.addDisk.hasData')}</Badge>
								{#if disk.data_description}
									<span class="block text-xs text-secondary break-words">{disk.data_description}</span>
								{/if}
							{:else}
								<span class="block text-xs text-secondary">{$t('storage.disks.empty')}</span>
							{/if}
						</span>
					</label>
				{/each}
			</fieldset>
			{#if selected?.has_data}
				<Alert variant="warning" title={$t('storage.addDisk.dataTitle')}>
					{$t('storage.addDisk.data')}
					<label class="flex items-center gap-2 mt-3 text-content cursor-pointer">
						<input type="checkbox" bind:checked={erase} class="form-checkbox" disabled={busy} />
						<span class="break-all">{$t('storage.addDisk.erase', { path: selected.path })}</span>
					</label>
				</Alert>
			{/if}
			{#if selected}
				<Input
					label={$t('storage.addDisk.confirm')}
					bind:value={confirm}
					placeholder={selected.path}
					autocomplete="off"
					spellcheck={false}
					disabled={busy}
					error={confirm && !confirmed ? $t('storage.addDisk.mismatch') : ''}
				/>
			{/if}
		{/if}
		{#if error}
			<Alert variant="error" title={$t('storage.addDisk.failed')}><span class="break-words">{error}</span></Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={busy} on:click={close}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={busy} disabled={!canAdd} on:click={add}>
			{$t('storage.addDisk.submit')}
		</Button>
	</svelte:fragment>
</Modal>
