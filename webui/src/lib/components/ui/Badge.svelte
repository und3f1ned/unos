<script lang="ts">
	import type { Snippet } from 'svelte';

	type Props = {
		variant?: 'success' | 'warning' | 'error' | 'info' | 'neutral' | 'purple' | 'cyan' | 'orange';
		/** A 6 px dot of the colour before the text, for a state (running, healthy). */
		dot?: boolean;
		class?: string;
		children?: Snippet;
	};

	let { variant = 'neutral', dot = false, class: className = '', children }: Props = $props();

	const variantClasses = {
		success: 'badge-success',
		warning: 'badge-warning',
		error: 'badge-error',
		info: 'badge-info',
		neutral: 'badge-neutral',
		purple: 'badge-purple',
		cyan: 'badge-cyan',
		orange: 'badge-orange'
	};

	const classes = $derived(`badge ${variantClasses[variant]} ${className || ''}`);
</script>

<span class={classes}>
	{#if dot}<span class="badge-dot" aria-hidden="true"></span>{/if}{@render children?.()}
</span>
