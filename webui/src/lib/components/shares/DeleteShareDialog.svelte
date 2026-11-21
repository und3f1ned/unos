<script lang="ts">
	import { deleteSmbShare, type SmbShare } from '$lib/api/shares';
	import { Alert, ConfirmDialog } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { messageOf } from './errors';
	import type { ShareOutcome } from './outcome.svelte';
	import type { SmbShares } from './smb.svelte';

	// The question before a share is deleted. Always mounted: the page asks with open(share).
	type Props = {
		/** The list, read again after the deletion. */
		smb: SmbShares;
		/** Takes the outcome of the deletion. */
		outcome: ShareOutcome;
	};

	let { smb, outcome }: Props = $props();

	// The share asked about; null while the dialog is closed.
	let share: SmbShare | null = $state(null);
	let deleting = $state(false);
	let error = $state('');

	/** Asks about `target`, without an error; also while it asks about another share. */
	export function open(target: SmbShare) {
		share = target;
		error = '';
	}

	function close() {
		if (deleting) return;
		share = null;
		error = '';
	}

	async function confirm() {
		if (!share) return;
		const name = share.name;
		deleting = true;
		error = '';
		try {
			const result = await deleteSmbShare(name);
			deleting = false;
			close();
			outcome.show($t('shares.smb.deleted', { name }), result.warnings);
			await smb.reload();
		} catch (e) {
			error = messageOf(e);
		} finally {
			deleting = false;
		}
	}
</script>

<ConfirmDialog
	open={share !== null}
	title={$t('shares.smb.deleteTitle')}
	busy={deleting}
	confirmLabel={$t('shares.smb.delete')}
	oncancel={close}
	onconfirm={confirm}
>
	{#if share}
		<!-- In parts around the share's name: ADR-0296. -->
		<p>
			{$t('shares.smb.deleteBefore')}
			<strong class="text-content">{share.name}</strong>{$t('shares.smb.deleteAfter')}
		</p>
		{#if error}
			<Alert variant="error" title={$t('shares.smb.notDeleted')}>{error}</Alert>
		{/if}
	{/if}
</ConfirmDialog>
