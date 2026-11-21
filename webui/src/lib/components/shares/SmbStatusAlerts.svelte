<script lang="ts">
	import type { SmbServiceStatus, SmbShare } from '$lib/api/shares';
	import { Alert, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		/** The SMB service as the server reports it; null when it could not be read. */
		status: SmbServiceStatus | null;
		/** Why the status could not be read. */
		error: string;
		/** The shares of the list: a share's own problem is said on its card, not again here. */
		shares?: SmbShare[];
		/** The report of the import of the old smb.conf was dismissed. */
		ondismissimport: () => void;
	};

	let { status, error, shares = [], ondismissimport }: Props = $props();

	// "Share old is not served: ..." as the server words it (supervisor/src/shares/service.py): the
	// card of that share says the same with its badge and its folder's problem.
	const problems = $derived(
		(status?.problems ?? []).filter(
			(problem) => !shares.some((share) => !share.served && problem.startsWith(`Share ${share.name} is not served:`))
		)
	);
	// What is worth knowing but needs no action now, in one panel instead of an alert each.
	const notes = $derived(
		status
			? [
					status.active_smb_users === 0 ? $t('shares.status.nobody') : '',
					status.unmanaged_samba_users.length > 0
						? $t('shares.status.unmanagedUsers', { users: status.unmanaged_samba_users.join(', ') })
						: ''
				].filter(Boolean)
			: []
	);
</script>

{#if status}
	{#if problems.length > 0}
		<Alert variant="error">
			{#each problems as problem}
				<p class="break-words">{problem}</p>
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
		<Alert variant="warning" size="sm">
			{$t('shares.status.guestShares', { shares: status.guest_shares.join(', ') })}
		</Alert>
	{/if}
	{#if notes.length > 0 || status.import_report.length > 0}
		<Alert variant="info" size="sm">
			<ul class="space-y-1">
				{#each notes as note (note)}
					<li class="break-words">{note}</li>
				{/each}
				{#if status.import_report.length > 0}
					<li>
						<!-- design-ignore: the smb.conf import's report, a list of lines inside the alert that shares.spec.ts opens by its summary -->
						<details>
							<summary class="cursor-pointer">
								{$t('shares.status.imported', { n: status.import_report.length })}
							</summary>
							<ul class="mt-1 list-disc space-y-0.5 pl-5">
								{#each status.import_report as line}
									<li class="break-words">{line}</li>
								{/each}
							</ul>
							<Button variant="secondary" size="sm" class="mt-2" onclick={ondismissimport}>
								{$t('shares.status.dismissImport')}
							</Button>
						</details>
					</li>
				{/if}
			</ul>
		</Alert>
	{/if}
{:else if error}
	<Alert variant="error" title={$t('shares.status.error')}>{error}</Alert>
{/if}
