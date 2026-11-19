<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import { deleteSmbShares, type SmbShare } from '$lib/api/shares';
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import DangerDialog from './DangerDialog.svelte';
	import { messageOf } from './errors';
	import type { ShareOutcome } from './outcome.svelte';
	import { BULK_DELETE_MAX, keyOf, type SmbShares } from './smb.svelte';

	// The one question of a deletion of several shares (ADR-0389). Always mounted: the list asks
	// with open().
	type Props = {
		/** The list: its selection, and `bulkDeleting` while the request runs. */
		smb: SmbShares;
		/** Takes the outcome of the deletion. */
		outcome: ShareOutcome;
	};

	let { smb, outcome }: Props = $props();

	// The shares as the page had them when it was asked, each with the version and the folder that
	// the request sends (a share changed since is not deleted); null while the dialog is closed.
	let shares: SmbShare[] | null = $state(null);
	let error = $state('');
	let errorTitle = $state('');

	/** Asks about the selected shares, unless none or too many are selected. */
	export function open() {
		const chosen = smb.chosen();
		if (chosen.length === 0 || chosen.length > BULK_DELETE_MAX) return;
		shares = chosen;
		error = '';
		outcome.notDeleted = [];
	}

	function close() {
		if (smb.bulkDeleting) return;
		shares = null;
		error = '';
	}

	async function confirm() {
		const asked = shares;
		if (!asked || smb.bulkDeleting) return;
		smb.bulkDeleting = true;
		error = '';
		try {
			const answer = await deleteSmbShares(
				asked.map((share) => ({
					name: share.name,
					expected_version: share.version,
					expected_path: share.path
				}))
			);
			const deleted = answer.results.filter((result) => result.status === 'deleted');
			const notDeleted = answer.results.filter((result) => result.status !== 'deleted');
			outcome.notDeleted = notDeleted;
			// What was not deleted stays selected, for another look; the list drops what is gone.
			const kept = new Set(notDeleted.map((result) => result.name));
			smb.selected = new Set(asked.filter((share) => kept.has(share.name)).map(keyOf));
			smb.bulkDeleting = false;
			close();
			outcome.notice =
				deleted.length > 0
					? $t('shares.bulk.deleted', {
							n: deleted.length,
							names: deleted.map((result) => result.name).join(', ')
						})
					: '';
			outcome.warnings = answer.warnings;
			outcome.error = '';
			await smb.reload();
		} catch (e) {
			// The session ended: the sign-in page is on its way.
			if (e instanceof ApiError && e.status === 401) return;
			// The dialog stays open with the reason. Whether nothing was deleted, the list read again
			// says: a refusal of the apply deletes none, but an answer that never came (a lost
			// connection) or an error after the deletion (its audit entry) does not say so.
			const reason = messageOf(e);
			await smb.reload();
			const listed = new Set(smb.shares.map(keyOf));
			if (smb.error) errorTitle = $t('shares.bulk.notKnown');
			else if (asked.every((share) => listed.has(keyOf(share)))) errorTitle = $t('shares.bulk.noneDeleted');
			else errorTitle = $t('shares.bulk.someGone');
			error = reason;
		} finally {
			smb.bulkDeleting = false;
		}
	}
</script>

<DangerDialog
	open={shares !== null}
	title={$t('shares.bulk.deleteTitle', { n: shares?.length ?? 0 })}
	busy={smb.bulkDeleting}
	confirmLabel={$t('shares.bulk.confirm', { n: shares?.length ?? 0 })}
	onclose={close}
	onconfirm={confirm}
>
	{#if shares}
		<!-- One question that names every share and its folder (ADR-0389). -->
		<div class="space-y-3">
			<p>{$t('shares.bulk.question', { n: shares.length })}</p>
			<ul class="space-y-1" aria-label={$t('shares.bulk.list')}>
				{#each shares as share (share.name)}
					<li class="min-w-0">
						<strong class="text-content break-all">{share.name}</strong>{' '}<span
							class="mono-xs text-muted break-all">{share.path}</span
						>
					</li>
				{/each}
			</ul>
			<p class="text-sm text-secondary">{$t('shares.bulk.kept', { n: shares.length })}</p>
			{#if error}
				<Alert variant="error" title={errorTitle}>{error}</Alert>
			{/if}
		</div>
	{/if}
</DangerDialog>
