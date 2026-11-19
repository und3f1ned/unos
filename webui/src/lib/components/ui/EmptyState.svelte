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
		/** A list inside a card: less room above and below. */
		compact?: boolean;
		class?: string;
		children?: Snippet;
	};

	let {
		icon = Inbox,
		title = undefined,
		description = '',
		compact = false,
		class: className = '',
		children
	}: Props = $props();

	const Icon = $derived(icon);
</script>

<div class="empty-state {compact ? 'empty-state-compact' : ''} {className || ''}">
	<span class="empty-state-icon-wrap" aria-hidden="true">
		<Icon class="empty-state-icon" />
	</span>
	<h3 class="empty-state-title">{title ?? $t('ui.emptyState.title')}</h3>
	{#if description}
		<p class="empty-state-description">{description}</p>
	{/if}
	{#if children}
		<div class="mt-4 flex flex-wrap justify-center gap-2">
			{@render children()}
		</div>
	{/if}
</div>
