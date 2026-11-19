<script lang="ts">
	// Add a free disk to a mounted pool (ADR-0411): the server offers the whole disks that hold
	// neither the system, nor a btrfs filesystem, nor a mount; the add erases the disk, once its path
	// is typed again and, for a disk with data, the erase is confirmed.
	import { untrack } from 'svelte';
	import { Alert, Badge, Button, Input, Modal } from '$lib/components/ui';
	import { addPoolDisk, getPoolDevices, type DiskCandidate, type Pool } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { messageOf } from './pools';

	type Props = {
		/** The pool to add a disk to; the dialog is open while it is set. */
		pool?: Pool | null;
		/** A size in the language of the page. */
		formatBytes: (bytes: number) => string;
		/** A profile in the language of the page. */
		profileLabel: (profile: string) => string;
		onclose?: () => void;
		/** The disk was added: the pool as it is now. */
		onadded?: (pool: Pool) => void;
	};

	let { pool = null, formatBytes, profileLabel, onclose, onadded }: Props = $props();

	// The pool whose disks were asked for last: no state, as only the code compares it.
	let loadedFor: Pool | null = null;
	let loading = $state(false);
	let loadError = $state('');
	let candidates: DiskCandidate[] = $state.raw([]);
	let profiles: string[] = $state.raw([]);
	let selectedPath = $state('');
	let confirm = $state('');
	let erase = $state(false);
	let busy = $state(false);
	let error = $state('');

	// A new pool asks for its disks, before the dialog draws, so that it opens on the loading text.
	$effect.pre(() => {
		const target = pool;
		untrack(() => {
			if (target !== loadedFor) load(target);
		});
	});
	let selected = $derived(candidates.find((disk) => disk.path === selectedPath) ?? null);
	let confirmed = $derived(selected !== null && confirm.trim() === selected.path);
	let canAdd = $derived(confirmed && (!selected?.has_data || erase) && !busy);

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
			if (loadedFor === target) loadError = messageOf(e);
		} finally {
			if (loadedFor === target) loading = false;
		}
	}

	// Each disk's erase is confirmed on its own: choosing another disk takes it back.
	function forgetErase() {
		erase = false;
	}

	function close() {
		if (!busy) onclose?.();
	}

	async function add() {
		if (!pool || !selected || !canAdd) return;
		busy = true;
		error = '';
		try {
			const grown = await addPoolDisk(pool.name, selected, confirm.trim(), erase);
			busy = false;
			onadded?.(grown);
		} catch (e) {
			error = messageOf(e);
			busy = false;
		}
	}
</script>

<Modal
	open={pool !== null}
	title={$t('storage.addDisk.title', { name: pool?.name ?? '' })}
	size="md"
	closable={!busy}
	onclose={close}
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
							onchange={forgetErase}
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

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={close}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={busy} disabled={!canAdd} onclick={add}>
			{$t('storage.addDisk.submit')}
		</Button>
	{/snippet}
</Modal>
