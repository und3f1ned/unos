<script lang="ts">
	import type { SmbServiceStatus } from '$lib/api/shares';
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		/** The SMB service as the server reports it; null when it could not be read. */
		status: SmbServiceStatus | null;
		/** Why the status could not be read. */
		error: string;
		/** The report of the import of the old smb.conf was dismissed. */
		ondismissimport: () => void;
	};

	let { status, error, ondismissimport }: Props = $props();
</script>

{#if status}
	{#if status.problems.length > 0}
		<Alert variant="error">
			{#each status.problems as problem}
				<p>{problem}</p>
			{/each}
		</Alert>
	{/if}
	{#if status.samba_tools_missing && status.samba_tools_missing.length > 0}
		<Alert variant="warning">
			<p>{$t('shares.status.sambaMissing')}</p>
			<p class="text-sm mt-1">
				{$t('shares.status.missing', { tools: status.samba_tools_missing.join(', ') })}
			</p>
		</Alert>
	{/if}
	{#if status.guest_shares.length > 0}
		<Alert variant="warning">
			{$t('shares.status.guestShares', { shares: status.guest_shares.join(', ') })}
		</Alert>
	{/if}
	{#if status.import_report.length > 0}
		<Alert variant="info" size="sm" title={$t('shares.status.imported')} dismissible ondismiss={ondismissimport}>
			{#each status.import_report as line}
				<p>{line}</p>
			{/each}
		</Alert>
	{/if}
	{#if status.unmanaged_samba_users.length > 0}
		<Alert variant="info" size="sm">
			{$t('shares.status.unmanagedUsers', { users: status.unmanaged_samba_users.join(', ') })}
		</Alert>
	{/if}
	{#if status.active_smb_users === 0}
		<Alert variant="info" size="sm">{$t('shares.status.nobody')}</Alert>
	{/if}
{:else if error}
	<Alert variant="error" title={$t('shares.status.error')}>{error}</Alert>
{/if}
