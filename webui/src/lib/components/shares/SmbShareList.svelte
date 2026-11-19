<script lang="ts">
	import { FolderOpen, Trash2 } from 'lucide-svelte';
	import type { SmbShare } from '$lib/api/shares';
	import { Alert, Button, Card, EmptyState } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { isRepairOf, repairJob } from '$lib/stores/repair';
	import ShareCard from './ShareCard.svelte';
	import { BULK_DELETE_MAX, keyOf, type SmbShares } from './smb.svelte';

	type Props = {
		/** The shares, their selection and the changes made from the list. */
		smb: SmbShares;
		/** The NAS's address as the browser reached it, for the connection hints. */
		host: string;
		// The buttons of a share's card: the page opens the dialog.
		onedit: (share: SmbShare) => void;
		onrepair: (share: SmbShare) => void;
		ondelete: (share: SmbShare) => void;
		// The selected shares are to be deleted: the page asks first.
		onbulkdelete: () => void;
	};

	let { smb, host, onedit, onrepair, ondelete, onbulkdelete }: Props = $props();
</script>

<Card>
	<h2 class="section-title mb-4">
		<FolderOpen class="w-5 h-5" />
		{$t('shares.smb.title')}
	</h2>

	{#if smb.error}
		<Alert variant="error" title={$t('shares.smb.loadError')}>{smb.error}</Alert>
	{:else if smb.shares.length === 0}
		<EmptyState icon={FolderOpen} title={$t('shares.smb.empty')} description={$t('shares.smb.emptyHint')} />
	{:else}
		<!-- The selection for an action on several shares (ADR-0389); it wraps on a phone. -->
		<div
			class="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2"
			role="toolbar"
			aria-label={$t('shares.bulk.toolbar')}
		>
			<label class="flex items-center gap-2 text-sm">
				<input
					type="checkbox"
					class="form-checkbox"
					checked={smb.allSelected}
					disabled={smb.bulkDeleting}
					onchange={(event) => smb.selectAll(event.currentTarget.checked)}
				/>
				{$t('shares.bulk.selectAll')}
			</label>
			<span class="text-sm text-content-secondary">
				{$t('shares.bulk.selected', { n: smb.selected.size })}
			</span>
			<Button
				variant="danger"
				size="sm"
				icon={Trash2}
				disabled={smb.selected.size === 0 || smb.selected.size > BULK_DELETE_MAX || smb.bulkDeleting}
				onclick={() => onbulkdelete()}
			>
				{$t('shares.bulk.delete')}
			</Button>
			{#if smb.selected.size > BULK_DELETE_MAX}
				<p class="w-full text-sm text-warning">
					{$t('shares.bulk.tooMany', { max: BULK_DELETE_MAX })}
				</p>
			{/if}
		</div>
		<div class="space-y-4">
			{#each smb.shares as share (share.name)}
				<ShareCard
					{share}
					{host}
					selected={smb.selected.has(keyOf(share))}
					selectDisabled={smb.bulkDeleting}
					onselect={(change) => smb.select(change.share, change.selected)}
					dismissing={smb.dismissing[share.name] ?? false}
					repairing={$repairJob?.state === 'running' && isRepairOf($repairJob, share.name)}
					{onedit}
					{onrepair}
					{ondelete}
					ondismissnotes={(share) => smb.dismissNotes(share)}
				/>
			{/each}
		</div>
	{/if}

	<p class="form-hint mt-4">{$t('shares.smb.networkNote')}</p>
</Card>
