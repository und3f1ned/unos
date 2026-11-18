<script lang="ts">
	// Remove a disk from a mounted pool (ADR-0410): its data moves to the other disks first, in a
	// unit of the host. The server says why a disk cannot leave (the only disk, the profile, the
	// space); the removal starts once the disk's path is typed again.
	import { createEventDispatcher } from 'svelte';
	import { Alert, Badge, Button, Input, Modal } from '$lib/components/ui';
	import {
		getPoolDevices,
		removePoolDisk,
		type DiskRemovalStatus,
		type Pool,
		type PoolMember
	} from '$lib/api/storage';
	import { t } from '$lib/i18n';

	/** The pool to remove a disk from; the dialog is open while it is set. */
	export let pool: Pool | null = null;
	/** A size in the language of the page. */
	export let formatBytes: (bytes: number) => string;

	const dispatch = createEventDispatcher<{ close: void; started: DiskRemovalStatus }>();

	let loadedFor: Pool | null = null;
	let loading = false;
	let loadError = '';
	let members: PoolMember[] = [];
	let selectedDevid: number | null = null;
	let confirm = '';
	let busy = false;
	let error = '';

	$: if (pool !== loadedFor) load(pool);
	$: selected = members.find((member) => member.devid === selectedDevid && member.problem === null) ?? null;
	$: confirmed = selected !== null && confirm.trim() === selected.path;
	$: canRemove = confirmed && !busy;

	async function load(target: Pool | null) {
		loadedFor = target;
		members = [];
		selectedDevid = null;
		confirm = '';
		error = '';
		loadError = '';
		if (!target) return;
		loading = true;
		try {
			const found = await getPoolDevices(target.name);
			if (loadedFor !== target) return;
			members = found.members;
		} catch (e) {
			if (loadedFor === target) loadError = e instanceof Error ? e.message : String(e);
		} finally {
			if (loadedFor === target) loading = false;
		}
	}

	function close() {
		if (!busy) dispatch('close');
	}

	async function remove() {
		if (!pool || !selected || !canRemove) return;
		busy = true;
		error = '';
		try {
			const started = await removePoolDisk(pool.name, selected, confirm.trim());
			busy = false;
			dispatch('started', started);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			busy = false;
		}
	}
</script>

<Modal
	open={pool !== null}
	title={$t('storage.removeDisk.title', { name: pool?.name ?? '' })}
	size="md"
	closable={!busy}
	on:close={close}
>
	{#if pool}
		<p class="text-sm text-secondary">{$t('storage.removeDisk.intro')}</p>
		{#if loading}
			<p class="text-muted text-sm">{$t('storage.removeDisk.loading')}</p>
		{:else if loadError}
			<Alert variant="error" title={$t('storage.removeDisk.loadError')}>{loadError}</Alert>
		{:else}
			<fieldset class="space-y-2 min-w-0">
				<legend class="form-label">{$t('storage.removeDisk.disk')}</legend>
				{#each members as member (member.devid)}
					<label
						class="flex items-start gap-3 p-2 bg-surface-muted rounded-lg {member.problem
							? 'opacity-60 cursor-not-allowed'
							: 'cursor-pointer hover:bg-surface-overlay'}"
					>
						<input
							type="radio"
							name="remove-disk"
							class="mt-1 border-border bg-surface-overlay text-brand focus:ring-brand-ring"
							value={member.devid}
							bind:group={selectedDevid}
							disabled={busy || member.problem !== null}
						/>
						<span class="flex-1 min-w-0">
							<span class="flex flex-wrap items-center gap-x-2">
								<span class="mono break-all">{member.path}</span>
								<span class="text-secondary text-sm">{formatBytes(member.size)}</span>
								{#if member.model}<span class="text-muted text-xs">{member.model}</span>{/if}
								{#if member.serial}<span class="mono-xs text-muted">{member.serial}</span>{/if}
							</span>
							<span class="block text-xs text-secondary">
								{$t('storage.removeDisk.used', { used: formatBytes(member.used) })}
							</span>
							{#if member.problem}
								<Badge variant="error" class="mt-1">{$t('storage.removeDisk.cannot')}</Badge>
								<span class="block text-xs text-secondary break-words">{member.problem}</span>
							{/if}
						</span>
					</label>
				{/each}
			</fieldset>
			{#if selected}
				<Input
					label={$t('storage.removeDisk.confirm')}
					bind:value={confirm}
					placeholder={selected.path}
					autocomplete="off"
					spellcheck={false}
					disabled={busy}
					error={confirm && !confirmed ? $t('storage.removeDisk.mismatch') : ''}
				/>
			{/if}
		{/if}
		{#if error}
			<Alert variant="error" title={$t('storage.removeDisk.failed')}><span class="break-words">{error}</span></Alert>
		{/if}
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={busy} on:click={close}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={busy} disabled={!canRemove} on:click={remove}>
			{$t('storage.removeDisk.submit')}
		</Button>
	</svelte:fragment>
</Modal>
