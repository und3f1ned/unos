<script lang="ts">
	import type { Snippet } from 'svelte';
	import { X } from 'lucide-svelte';
	import { t } from '$lib/i18n';
	import Button from './Button.svelte';

	// The bar of a selection (ADR-0432): a toolbar named by `label` with the count and, only while
	// something is selected, the actions on it and Clear selection; no action waits disabled (a red
	// Delete that cannot be pressed). It keeps its height, so that the list under it does not move
	// when a click selects, and while something is selected it sticks to the top of what scrolls.
	// `leading` is what stays at its start, such as a Select all checkbox.
	type Props = {
		count: number;
		/** The toolbar's name for screen readers: "Selected items". */
		label: string;
		/** The count as a text of the area ("2 containers selected"); "{n} selected" by default. */
		summary?: string;
		onclear?: (() => void) | null;
		leading?: Snippet;
		/** The actions on the selected items. */
		children?: Snippet;
		class?: string;
	};

	let { count, label, summary = '', onclear = null, leading, children, class: className = '' }: Props = $props();
</script>

<div class="bulk-bar {className}" role="toolbar" aria-label={label} data-active={count > 0 ? '' : undefined}>
	{@render leading?.()}
	<span class="bulk-bar-count" aria-live="polite">{summary || $t('ui.bulk.selected', { n: count })}</span>
	{#if count > 0}
		<div class="flex flex-wrap items-center gap-2">
			{@render children?.()}
		</div>
		{#if onclear}
			<Button variant="ghost" size="sm" icon={X} class="ml-auto" onclick={onclear}>{$t('ui.bulk.clear')}</Button>
		{/if}
	{/if}
</div>
