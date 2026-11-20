<script lang="ts">
	import { deleteNfsExport, type NfsExport } from '$lib/api/shares';
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import DangerDialog from './DangerDialog.svelte';
	import { messageOf } from './errors';
	import type { NfsExports } from './nfs.svelte';
	import type { ShareOutcome } from './outcome.svelte';

	// The question before an export is deleted. Always mounted: the page asks with open(exp).
	type Props = {
		/** The exports, read again after the deletion. */
		nfs: NfsExports;
		/** Takes the outcome of the deletion. */
		outcome: ShareOutcome;
	};

	let { nfs, outcome }: Props = $props();

	// The export asked about; null while the dialog is closed.
	let exp: NfsExport | null = $state(null);
	let deleting = $state(false);
	let error = $state('');

	/** Asks about `target`, without an error; also while it asks about another export. */
	export function open(target: NfsExport) {
		exp = target;
		error = '';
	}

	function close() {
		if (!deleting) exp = null;
	}

	async function confirm() {
		if (!exp) return;
		const path = exp.path;
		deleting = true;
		error = '';
		try {
			const result = await deleteNfsExport(path);
			deleting = false;
			exp = null;
			outcome.show($t('shares.nfs.deleted', { path }), result.warnings);
			await nfs.load();
		} catch (e) {
			error = messageOf(e);
		} finally {
			deleting = false;
		}
	}
</script>

<DangerDialog
	open={exp !== null}
	title={$t('shares.nfs.deleteTitle')}
	busy={deleting}
	confirmLabel={$t('shares.nfs.delete')}
	onclose={close}
	onconfirm={confirm}
>
	{#if exp}
		<!-- In parts around the folder's path: ADR-0296. -->
		<p>
			{$t('shares.nfs.deleteBefore')}
			<span class="mono break-all">{exp.path}</span>{$t('shares.nfs.deleteAfter')}
		</p>
		{#if error}
			<Alert variant="error" title={$t('shares.nfs.notDeleted')}>{error}</Alert>
		{/if}
	{/if}
</DangerDialog>
