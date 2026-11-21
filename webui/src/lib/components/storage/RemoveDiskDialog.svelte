<script lang="ts">
	// Remove a disk from a mounted pool (ADR-0411): its data moves to the other disks first, in a
	// unit of the host. The server says why a disk cannot leave (the only disk, the profile, the
	// space); the removal starts once the disk's path is typed again.
	import { untrack } from 'svelte';
	import { Alert, Badge, Button, ErrorState, Input, Modal, RadioGroup, Skeleton } from '$lib/components/ui';
	import { getPoolDevices, removePoolDisk, type DiskRemovalStatus, type Pool, type PoolMember } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { messageOf } from './pools';

	type Props = {
		/** The pool to remove a disk from; the dialog is open while it is set. */
		pool?: Pool | null;
		/** A size in the language of the page. */
		formatBytes: (bytes: number) => string;
		onclose?: () => void;
		/** The removal started: its status. */
		onstarted?: (status: DiskRemovalStatus) => void;
	};

	let { pool = null, formatBytes, onclose, onstarted }: Props = $props();

	// The pool whose disks were asked for last: no state, as only the code compares it.
	let loadedFor: Pool | null = null;
	let loading = $state(false);
	let loadError = $state('');
	let members: PoolMember[] = $state.raw([]);
	// The chosen disk's devid, as the RadioGroup's value; '' for none.
	let selectedDevid = $state('');
	let confirm = $state('');
	let busy = $state(false);
	let error = $state('');

	// A new pool asks for its disks, before the dialog draws, so that it opens on the loading text.
	$effect.pre(() => {
		const target = pool;
		untrack(() => {
			if (target !== loadedFor) load(target);
		});
	});
	let selected = $derived(
		members.find((member) => String(member.devid) === selectedDevid && member.problem === null) ?? null
	);
	let confirmed = $derived(selected !== null && confirm.trim() === selected.path);
	let canRemove = $derived(confirmed && !busy);
	const options = $derived(
		members.map((member) => ({ value: String(member.devid), label: member.path, disabled: member.problem !== null }))
	);

	async function load(target: Pool | null) {
		loadedFor = target;
		members = [];
		selectedDevid = '';
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
			if (loadedFor === target) loadError = messageOf(e);
		} finally {
			if (loadedFor === target) loading = false;
		}
	}

	function close() {
		if (!busy) onclose?.();
	}

	async function remove() {
		if (!pool || !selected || !canRemove) return;
		busy = true;
		error = '';
		try {
			const started = await removePoolDisk(pool.name, selected, confirm.trim());
			busy = false;
			onstarted?.(started);
		} catch (e) {
			error = messageOf(e);
			busy = false;
		}
	}
</script>

<Modal
	open={pool !== null}
	title={$t('storage.removeDisk.title', { name: pool?.name ?? '' })}
	size="md"
	closable={!busy}
	onclose={close}
>
	{#if pool}
		<p class="text-sm text-secondary">{$t('storage.removeDisk.intro')}</p>
		{#if loading}
			<!-- The disks' place while they are read (ADR-0430). -->
			<div aria-busy="true"><Skeleton shape="block" class="h-16 w-full" /></div>
		{:else if loadError}
			<ErrorState title={$t('storage.removeDisk.loadError')} message={loadError} onretry={() => load(pool)} />
		{:else}
			<RadioGroup
				name="remove-disk"
				legend={$t('storage.removeDisk.disk')}
				{options}
				bind:value={selectedDevid}
				disabled={busy}
			>
				{#snippet item(option)}
					{@const member = members.find((candidate) => String(candidate.devid) === option.value)}
					{#if member}
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
					{/if}
				{/snippet}
			</RadioGroup>
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

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={close}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={busy} disabled={!canRemove} onclick={remove}>
			{$t('storage.removeDisk.submit')}
		</Button>
	{/snippet}
</Modal>
