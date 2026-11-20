<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Inbox } from 'lucide-svelte';
	import { t } from '$lib/i18n';
	import Button from './Button.svelte';
	import type { IconComponent } from './types';

	type Props = {
		icon?: IconComponent;
		// Without a title: "No data" in the language of the page.
		title?: string | undefined;
		description?: string;
		/** A list inside a card, whose heading names it: less room, and a title that is no heading. */
		compact?: boolean;
		/**
		 * The next step (ADR-0432): a button, or a link with `href`, such as "Create a group" where
		 * the groups are made. Not a second copy of the page header's own action (ADR-0431).
		 */
		action?: { label: string; onclick?: () => void; href?: string; icon?: IconComponent } | null;
		class?: string;
		children?: Snippet;
	};

	let {
		icon = Inbox,
		title = undefined,
		description = '',
		compact = false,
		action = null,
		class: className = '',
		children
	}: Props = $props();

	const Icon = $derived(icon);
</script>

<div class="empty-state {compact ? 'empty-state-compact' : ''} {className || ''}">
	<span class="empty-state-icon-wrap" aria-hidden="true">
		<Icon class="empty-state-icon" />
	</span>
	{#if compact}
		<p class="empty-state-title">{title ?? $t('ui.emptyState.title')}</p>
	{:else}
		<h3 class="empty-state-title">{title ?? $t('ui.emptyState.title')}</h3>
	{/if}
	{#if description}
		<p class="empty-state-description">{description}</p>
	{/if}
	{#if children || action}
		<div class="mt-4 flex flex-wrap justify-center gap-2">
			{#if action}
				<Button
					variant="secondary"
					size="sm"
					href={action.href ?? null}
					icon={action.icon ?? null}
					onclick={action.onclick}>{action.label}</Button
				>
			{/if}
			{@render children?.()}
		</div>
	{/if}
</div>
