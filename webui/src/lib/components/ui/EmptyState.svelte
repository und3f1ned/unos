<script lang="ts">
	import type { ComponentType } from 'svelte';
	import { Inbox } from 'lucide-svelte';
	import { t } from '$lib/i18n';

	export let icon: ComponentType = Inbox;
	// Without a title: "No data" in the language of the page.
	export let title: string | undefined = undefined;
	export let description: string = '';
</script>

<div class="empty-state {$$props.class || ''}">
	<svelte:component this={icon} class="empty-state-icon" />
	<h3 class="empty-state-title">{title ?? $t('ui.emptyState.title')}</h3>
	{#if description}
		<p class="empty-state-description">{description}</p>
	{/if}
	{#if $$slots.default}
		<div class="mt-4">
			<slot />
		</div>
	{/if}
</div>
