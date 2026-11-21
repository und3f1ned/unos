<script lang="ts">
	// The result of the last action on several containers that did not do everything, per container:
	// what failed and why stays to be read (ADR-0432); a run that did all is a toast of the page, and
	// the container a run is at is the bar's (BulkToolbar).
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { BULK_DONE, type BulkResult } from './bulk.svelte';
	import { labelOf, STATES } from './labels';

	type Props = {
		result: BulkResult | null;
		ondismiss: () => void;
	};

	let { result, ondismiss }: Props = $props();

	const ok = $derived(result ? result.items.filter((item) => item.state !== undefined).length : 0);
</script>

{#if result}
	{@const title = $t(BULK_DONE[result.action], { ok, n: result.items.length })}
	<Alert
		variant={ok === 0 ? 'error' : 'warning'}
		{title}
		dismissible
		{ondismiss}
	>
		<ul class="space-y-1 text-sm" aria-label={title}>
			{#each result.items as item (item.id)}
				<li class="break-words">
					{#if item.state !== undefined}
						{$t('containers.bulk.itemDone', { name: item.name, state: labelOf(STATES, item.state, $t) })}
					{:else if item.skipped}
						{$t('containers.bulk.itemNotRunning', { name: item.name })}
					{:else}
						{$t('containers.bulk.itemFailed', { name: item.name, reason: item.reason ?? '' })}
					{/if}
				</li>
			{/each}
		</ul>
	</Alert>
{/if}
