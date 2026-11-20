<script lang="ts">
	import type { NfsServiceStatus } from '$lib/api/shares';
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		/** The NFS server as the server reports it; null when it could not be read. */
		status: NfsServiceStatus | null;
		/** Why the status could not be read. */
		error: string;
	};

	let { status, error }: Props = $props();
</script>

{#if status}
	{#if status.server_missing}
		<Alert variant="warning">
			<p>{$t('shares.nfs.serverMissing')}</p>
			<p class="text-sm mt-1">{$t('shares.nfs.installServer')}</p>
		</Alert>
	{/if}
	{#if status.problems.length > 0}
		<!-- Lines that exportfs cannot read, and exports of UNOS below folders that other
		     accounts can change: each text names the file or the folder. -->
		<Alert variant="error">
			{#each status.problems as problem}
				<p>{problem}</p>
			{/each}
		</Alert>
	{/if}
{:else if error}
	<Alert variant="error" title={$t('shares.nfs.statusError')}>{error}</Alert>
{/if}
