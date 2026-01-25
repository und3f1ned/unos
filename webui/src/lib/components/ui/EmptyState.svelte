<script lang="ts">
	import type { ComponentType } from 'svelte';
	import { Inbox } from 'lucide-svelte';

	export let icon: ComponentType = Inbox;
	export let title: string = 'No data';
	export let description: string = '';
</script>

<div class="empty-state {$$props.class || ''}">
	<svelte:component this={icon} class="empty-state-icon" />
	<h3 class="empty-state-title">{title}</h3>
	{#if description}
		<p class="empty-state-description">{description}</p>
	{/if}
	{#if $$slots.default}
		<div class="mt-4">
			<slot />
		</div>
	{/if}
</div>
