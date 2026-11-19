<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Inbox } from 'lucide-svelte';
	import { t } from '$lib/i18n';
	import type { IconComponent } from './types';

	type Props = {
		icon?: IconComponent;
		// Without a title: "No data" in the language of the page.
		title?: string | undefined;
		description?: string;
		class?: string;
		children?: Snippet;
	};

	let { icon = Inbox, title = undefined, description = '', class: className = '', children }: Props = $props();

	const Icon = $derived(icon);
</script>

<div class="empty-state {className || ''}">
	<Icon class="empty-state-icon" />
	<h3 class="empty-state-title">{title ?? $t('ui.emptyState.title')}</h3>
	{#if description}
		<p class="empty-state-description">{description}</p>
	{/if}
	{#if children}
		<div class="mt-4">
			{@render children()}
		</div>
	{/if}
</div>
