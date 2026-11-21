<script lang="ts">
	// The dialog that takes a snapshot of the chosen pool or volume, under a name or the server's.
	import { Plus } from 'lucide-svelte';
	import { Alert, Button, Input, Modal } from '$lib/components/ui';
	import { createSnapshot, type Subvolume } from '$lib/api/snapshots';
	import { t } from '$lib/i18n';
	import { messageOf, SNAPSHOTS_FOLDER } from './names';

	type Props = {
		open: boolean;
		/** The pool or volume of the snapshot. */
		selected: Subvolume | null;
		onclose: () => void;
		/** The snapshot was taken. */
		oncreated: () => void | Promise<void>;
	};

	let { open, selected, onclose, oncreated }: Props = $props();

	let newSnapshotName = $state('');
	let creating = $state(false);
	let createError = $state('');

	// The dialog opens empty: its name and error are emptied when it closes, while it shows nothing.
	function closeCreate() {
		if (creating) return;
		newSnapshotName = '';
		createError = '';
		onclose();
	}

	async function handleCreate() {
		if (!selected || creating) return;
		creating = true;
		createError = '';
		try {
			// Without a name the server makes one (snap_<date>_<time>, in UTC).
			await createSnapshot(selected.path, newSnapshotName.trim() || undefined);
			creating = false;
			newSnapshotName = '';
			await oncreated();
		} catch (e) {
			// 400: the name is taken or invalid; 500: btrfs or the .snapshots folder failed
			createError = messageOf(e);
		} finally {
			creating = false;
		}
	}
</script>

<Modal {open} title={$t('snapshots.new.title')} closable={!creating} onclose={closeCreate}>
	{#if selected}
		<form
			onsubmit={(e) => {
				e.preventDefault();
				handleCreate();
			}}
			class="space-y-4"
		>
			<p class="text-sm text-secondary">
				{$t('snapshots.new.of')}
				<span class="mono-xs break-all">{selected.path}</span>{$t('snapshots.new.keptIn')}
				<span class="mono-xs break-all">{selected.path}/{SNAPSHOTS_FOLDER}</span>.
				{#if !selected.volume}
					{$t('snapshots.new.pool')}
				{/if}
			</p>
			<Input
				label={$t('snapshots.new.name')}
				bind:value={newSnapshotName}
				placeholder={$t('snapshots.new.namePlaceholder')}
				autocomplete="off"
				disabled={creating}
			/>
			{#if createError}
				<Alert variant="error" title={$t('snapshots.new.failed')}>{createError}</Alert>
			{/if}
		</form>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={creating} onclick={closeCreate}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" icon={Plus} loading={creating} onclick={handleCreate}>
			{$t('snapshots.new.submit')}
		</Button>
	{/snippet}
</Modal>
