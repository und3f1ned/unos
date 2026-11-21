<script lang="ts">
	// The dialog that restores a snapshot: its copy is made right in the pool or volume it was taken
	// of (ADR-0358), next to what is there.
	import { Alert, ConfirmDialog } from '$lib/components/ui';
	import { restoreSnapshot, type SnapshotInfo, type Subvolume } from '$lib/api/snapshots';
	import { t } from '$lib/i18n';
	import { messageOf, RESTORE_PREFIX } from './names';

	type Props = {
		/** The snapshot to restore; the dialog is open while it is set. */
		snapshot: SnapshotInfo | null;
		/** The mounted pools and their volumes. */
		subvolumes: Subvolume[];
		onclose: () => void;
		/** The copy was made: where it is. */
		onrestored: (target: string) => void | Promise<void>;
	};

	let { snapshot, subvolumes, onclose, onrestored }: Props = $props();

	let restoring = $state(false);
	let restoreError = $state('');

	// The pool or volume that a restore makes its copy in.
	let restoreInto = $derived(
		snapshot ? (subvolumes.find((sv) => sv.path === snapshot?.subvolume) ?? null) : null
	);

	function closeRestore() {
		if (restoring) return;
		restoreError = '';
		onclose();
	}

	async function handleRestore() {
		if (!snapshot || restoring) return;
		restoring = true;
		restoreError = '';
		try {
			const result = await restoreSnapshot(snapshot.path);
			restoring = false;
			// The copy of a pool's snapshot is a new volume of the pool: the page loads its lists again.
			await onrestored(result.target);
		} catch (e) {
			restoreError = messageOf(e);
		} finally {
			restoring = false;
		}
	}
</script>

<!-- A restore adds a copy and changes nothing that is there: a question, not a warning of loss. -->
<ConfirmDialog
	open={snapshot !== null}
	title={$t('snapshots.restore.title')}
	confirmLabel={$t('snapshots.restore.submit')}
	tone="neutral"
	busy={restoring}
	oncancel={closeRestore}
	onconfirm={handleRestore}
>
	{#if snapshot}
		<p class="text-sm">
			{$t('snapshots.restore.before')}
			<span class="font-semibold break-all">{snapshot.name}</span>{$t('snapshots.restore.copy')}
			<span class="mono-xs">{RESTORE_PREFIX}{$t('snapshots.dateTime')}</span>
			{$t('snapshots.restore.madeIn')}
			<span class="mono-xs break-all">{snapshot.subvolume}</span>{$t('snapshots.restore.after')}
		</p>
		{#if restoreInto && !restoreInto.volume}
			<p class="text-sm text-secondary">{$t('snapshots.restore.poolCopy')}</p>
		{:else if restoreInto}
			<p class="text-sm text-secondary">{$t('snapshots.restore.volumeCopy')}</p>
		{/if}
		{#if restoreError}
			<Alert variant="error" title={$t('snapshots.restore.failed')}>{restoreError}</Alert>
		{/if}
	{/if}
</ConfirmDialog>
