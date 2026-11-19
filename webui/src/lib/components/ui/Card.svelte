<script lang="ts">
	import type { Snippet } from 'svelte';

	// A card: surface-elevated with a 1 px border (ADR-0428), or a nested panel inside one. With a
	// `title` it draws its header, the title on the left and the `actions` on the right, wrapping on
	// a narrow screen. `flush` lets a table reach the card's edges.
	type Props = {
		nested?: boolean;
		padding?: 'none' | 'sm' | 'md' | 'lg';
		/** A card that holds a table, which reaches its edges. */
		flush?: boolean;
		title?: string;
		description?: string;
		/** The level of the title's heading: an h3 unless the card is a section of its own. */
		headingLevel?: 2 | 3;
		class?: string;
		actions?: Snippet;
		children?: Snippet;
	};

	let {
		nested = false,
		padding = 'md',
		flush = false,
		title = '',
		description = '',
		headingLevel = 3,
		class: className = '',
		actions,
		children
	}: Props = $props();

	const paddingClasses = $derived({
		none: 'p-0',
		sm: 'p-4',
		md: nested ? 'p-4' : 'p-5',
		lg: 'p-6'
	});

	const classes = $derived(
		[nested ? 'card-nested' : 'card', flush ? 'card-flush' : paddingClasses[padding], className]
			.filter(Boolean)
			.join(' ')
	);
</script>

<div class={classes}>
	{#if title}
		<div class="card-header">
			<div class="min-w-0">
				<svelte:element this={`h${headingLevel}`} class="card-title">{title}</svelte:element>
				{#if description}
					<p class="card-description">{description}</p>
				{/if}
			</div>
			{#if actions}
				<div class="flex flex-wrap items-center gap-2">
					{@render actions()}
				</div>
			{/if}
		</div>
	{/if}
	{@render children?.()}
</div>
