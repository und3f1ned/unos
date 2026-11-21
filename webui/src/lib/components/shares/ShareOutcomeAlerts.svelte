<script lang="ts">
	import type { SmbShareDeleteResult } from '$lib/api/shares';
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { ShareOutcome } from './outcome.svelte';

	type Props = {
		/** The outcome of the last change, each part shown until dismissed. */
		outcome: ShareOutcome;
	};

	let { outcome }: Props = $props();

	// The status the server answers for a share that was not deleted, through a key (ADR-0287).
	const NOT_DELETED: Record<string, string> = {
		not_found: 'shares.bulk.status.notFound',
		changed: 'shares.bulk.status.changed'
	};

	/** Why a share was not deleted, in the page's language; a status without a key as the server words it. */
	function notDeletedReason(result: SmbShareDeleteResult): string {
		return Object.prototype.hasOwnProperty.call(NOT_DELETED, result.status)
			? $t(NOT_DELETED[result.status])
			: (result.detail ?? result.status);
	}
</script>

{#if outcome.warnings.length > 0}
	<Alert variant="warning" dismissible ondismiss={() => (outcome.warnings = [])}>
		{#each outcome.warnings as warning}
			<p>{warning}</p>
		{/each}
	</Alert>
{/if}
{#if outcome.error}
	<Alert variant="error" dismissible ondismiss={() => (outcome.error = '')}>{outcome.error}</Alert>
{/if}
{#if outcome.notDeleted.length > 0}
	{@const title = $t('shares.bulk.notDeleted', { n: outcome.notDeleted.length })}
	<Alert variant="warning" {title} dismissible ondismiss={() => (outcome.notDeleted = [])}>
		<ul class="space-y-1" aria-label={title}>
			{#each outcome.notDeleted as result (result.name)}
				<li class="break-words">
					{$t('shares.bulk.notDeletedItem', { name: result.name, reason: notDeletedReason(result) })}
				</li>
			{/each}
		</ul>
	</Alert>
{/if}
