<script lang="ts">
	// The container that an action on several is at, and the result of the last one, per container.
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { BULK_DONE, BULK_PROGRESS, type BulkProgress, type BulkResult } from './bulk.svelte';
	import { labelOf, STATES } from './labels';

	type Props = {
		progress: BulkProgress | null;
		result: BulkResult | null;
		ondismiss: () => void;
	};

	let { progress, result, ondismiss }: Props = $props();

	const ok = $derived(result ? result.items.filter((item) => item.state !== undefined).length : 0);
</script>

{#if progress}
	<p class="text-sm text-secondary" role="status">
		{$t(BULK_PROGRESS[progress.action], {
			name: progress.name,
			i: progress.i,
			n: progress.n
		})}
	</p>
{/if}
{#if result}
	{@const title = $t(BULK_DONE[result.action], { ok, n: result.items.length })}
	<Alert
		variant={ok === result.items.length ? 'success' : ok === 0 ? 'error' : 'warning'}
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
