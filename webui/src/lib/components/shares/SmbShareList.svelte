<script lang="ts">
	import { FolderOpen, Trash2 } from 'lucide-svelte';
	import type { SmbShare } from '$lib/api/shares';
	import { BulkBar, Button, Card, Checkbox, EmptyState, ErrorState, HelpText } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { isRepairOf, repairJob } from '$lib/stores/repair';
	import ShareCard from './ShareCard.svelte';
	import { BULK_DELETE_MAX, keyOf, type SmbShares } from './smb.svelte';

	type Props = {
		/** The shares, their selection and the changes made from the list. */
		smb: SmbShares;
		/** The NAS's address as the browser reached it, for the connection hints. */
		host: string;
		/** No pool to share a folder of: the empty state leads to Storage. */
		noPool?: boolean;
		// The buttons of a share's card: the page opens the dialog.
		onedit: (share: SmbShare) => void;
		onrepair: (share: SmbShare) => void;
		ondelete: (share: SmbShare) => void;
		// The selected shares are to be deleted: the page asks first.
		onbulkdelete: () => void;
		/** Read the shares again after a failed load. */
		onretry: () => void;
	};

	let { smb, host, noPool = false, onedit, onrepair, ondelete, onbulkdelete, onretry }: Props = $props();

	let retrying = $state(false);

	async function retry() {
		retrying = true;
		try {
			await onretry();
		} finally {
			retrying = false;
		}
	}
</script>

<!-- The shares, each a card, under the tab that names them: no card around them repeats the tab. -->
<section class="space-y-4" aria-label={$t('shares.smb.title')}>
	{#if smb.error}
		<Card>
			<ErrorState title={$t('shares.smb.loadError')} message={smb.error} onretry={retry} {retrying} />
		</Card>
	{:else if smb.shares.length === 0}
		<!-- Its next step is the header's Add SMB share, which it names; without a pool, Storage. -->
		<Card>
			<EmptyState icon={FolderOpen} title={$t('shares.smb.empty')} description={$t('shares.smb.emptyHint')}>
				{#if noPool}
					<a class="link" href="/storage">{$t('shares.toStorage')}</a>
				{/if}
			</EmptyState>
		</Card>
	{:else}
		<!-- The selection for an action on several shares (ADR-0389): Delete selected shows only
		     with a selection, and confirms in its dialog. -->
		<BulkBar
			count={smb.selected.size}
			label={$t('shares.bulk.toolbar')}
			summary={$t('shares.bulk.selected', { n: smb.selected.size })}
		>
			{#snippet leading()}
				<Checkbox
					label={$t('shares.bulk.selectAll')}
					checked={smb.allSelected}
					indeterminate={smb.selected.size > 0 && !smb.allSelected}
					disabled={smb.bulkDeleting}
					onchange={(on) => smb.selectAll(on)}
				/>
			{/snippet}
			<Button
				variant="secondary"
				size="sm"
				icon={Trash2}
				disabled={smb.selected.size > BULK_DELETE_MAX || smb.bulkDeleting}
				onclick={() => onbulkdelete()}
			>
				{$t('shares.bulk.delete')}
			</Button>
			{#if smb.selected.size > BULK_DELETE_MAX}
				<p class="text-sm text-warning">
					{$t('shares.bulk.tooMany', { max: BULK_DELETE_MAX })}
				</p>
			{/if}
		</BulkBar>
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
	{/if}

	<HelpText text={$t('shares.smb.networkNote')} />
</section>
