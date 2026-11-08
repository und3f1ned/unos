<script lang="ts">
	import type { ComponentType } from 'svelte';
	import { Loader2 } from 'lucide-svelte';

	export let variant: 'primary' | 'secondary' | 'danger' | 'warning' | 'success' | 'ghost' = 'primary';
	export let size: 'sm' | 'md' | 'lg' | 'icon' | 'icon-sm' = 'md';
	export let type: 'button' | 'submit' | 'reset' = 'button';
	export let disabled: boolean = false;
	export let loading: boolean = false;
	export let icon: ComponentType | null = null;
	export let href: string | null = null;
	// A toggle button (aria-pressed): true while what it shows or turns on is active.
	export let pressed: boolean | undefined = undefined;

	const variantClasses = {
		primary: 'btn-primary',
		secondary: 'btn-secondary',
		danger: 'btn-danger',
		warning: 'btn-warning',
		success: 'btn-success',
		ghost: 'btn-ghost'
	};

	const sizeClasses = {
		sm: 'btn-sm',
		md: 'btn-md',
		lg: 'btn-lg',
		icon: 'btn-icon',
		'icon-sm': 'btn-icon-sm'
	};

	$: classes = `btn ${variantClasses[variant]} ${sizeClasses[size]} ${$$props.class || ''}`;
</script>

{#if href}
	<a {href} class={classes} class:disabled>
		{#if loading}
			<Loader2 class="w-4 h-4 loading-spinner" />
		{:else if icon}
			<svelte:component this={icon} class="w-4 h-4" />
		{/if}
		<slot />
	</a>
{:else}
	<button
		{type}
		disabled={disabled || loading}
		aria-pressed={pressed}
		class={classes}
		on:click
	>
		{#if loading}
			<Loader2 class="w-4 h-4 loading-spinner" />
		{:else if icon}
			<svelte:component this={icon} class="w-4 h-4" />
		{/if}
		<slot />
	</button>
{/if}
