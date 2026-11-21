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
		/**
		 * What selecting does here, in the actions' place while nothing is selected; the bar then keeps
		 * the height of its actions too.
		 */
		hint?: string;
		onclear?: (() => void) | null;
		leading?: Snippet;
		/** The actions on the selected items. */
		children?: Snippet;
		class?: string;
	};

	let { count, label, summary = '', hint = '', onclear = null, leading, children, class: className = '' }: Props =
		$props();
</script>

<div class="bulk-bar {className}" role="toolbar" aria-label={label} data-active={count > 0 ? '' : undefined}>
	{@render leading?.()}
	<span class="bulk-bar-count" aria-live="polite">{summary || $t('ui.bulk.selected', { n: count })}</span>
	{#if hint}
		<!-- The hint and the actions share one cell, unseen and inert while the other shows, so that
		     the bar is as tall either way, also where the actions wrap: actions that came on the first
		     click of a double-click would otherwise move the list under the pointer. -->
		<div class="grid flex-1 min-w-0">
			<div class="col-start-1 row-start-1 flex flex-wrap items-center gap-2 {count > 0 ? '' : 'invisible'}" inert={count === 0}>
				{@render children?.()}
				{#if onclear}
					<Button variant="ghost" size="sm" icon={X} onclick={onclear}>{$t('ui.bulk.clear')}</Button>
				{/if}
			</div>
			{#if count === 0}
				<span class="col-start-1 row-start-1 self-center text-xs text-content-muted">{hint}</span>
			{/if}
		</div>
	{:else if count > 0}
		<div class="flex flex-wrap items-center gap-2">
			{@render children?.()}
		</div>
		{#if onclear}
			<Button variant="ghost" size="sm" icon={X} class="ml-auto" onclick={onclear}>{$t('ui.bulk.clear')}</Button>
		{/if}
	{/if}
</div>
