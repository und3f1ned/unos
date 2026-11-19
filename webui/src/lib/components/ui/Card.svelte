<script lang="ts">
	import type { Snippet } from 'svelte';

	type Props = {
		nested?: boolean;
		padding?: 'none' | 'sm' | 'md' | 'lg';
		class?: string;
		children?: Snippet;
	};

	let { nested = false, padding = 'md', class: className = '', children }: Props = $props();

	const paddingClasses = {
		none: 'p-0',
		sm: 'p-3',
		md: 'p-4',
		lg: 'p-6'
	};

	const baseClass = $derived(nested ? 'card-nested' : 'card');
	const classes = $derived(
		`${baseClass} ${paddingClasses[padding]} ${className || ''}`.replace('p-4', paddingClasses[padding])
	);
</script>

<div class={classes}>
	{@render children?.()}
</div>

<style>
	/* Override default padding from .card if custom padding is specified */
	:global(.card-nested) {
		padding: inherit;
	}
</style>
