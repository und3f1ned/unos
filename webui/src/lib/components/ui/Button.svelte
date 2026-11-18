<script lang="ts">
	import type { ComponentType } from 'svelte';
	import { Loader2 } from 'lucide-svelte';

	// `plain` brings no look of its own: a control that styles itself through `class` (a tab, a
	// clickable card or row, the classes `tab-*` of app.css).
	export let variant: 'primary' | 'secondary' | 'danger' | 'warning' | 'success' | 'ghost' | 'plain' = 'primary';
	export let size: 'sm' | 'md' | 'lg' | 'icon' | 'icon-sm' = 'md';
	export let type: 'button' | 'submit' | 'reset' = 'button';
	export let disabled: boolean = false;
	export let loading: boolean = false;
	export let icon: ComponentType | null = null;
	export let href: string | null = null;
	// A toggle button (aria-pressed): true while what it shows or turns on is active.
	export let pressed: boolean | undefined = undefined;

	// The button element, for a page that moves the focus or measures it: bind:el.
	export let el: HTMLButtonElement | null = null;

	const variantClasses = {
		primary: 'btn-primary',
		secondary: 'btn-secondary',
		danger: 'btn-danger',
		warning: 'btn-warning',
		success: 'btn-success',
		ghost: 'btn-ghost',
		plain: ''
	};

	const sizeClasses = {
		sm: 'btn-sm',
		md: 'btn-md',
		lg: 'btn-lg',
		icon: 'btn-icon',
		'icon-sm': 'btn-icon-sm'
	};

	$: classes = variant === 'plain'
		? `${$$props.class || ''}`
		: `btn ${variantClasses[variant]} ${sizeClasses[size]} ${$$props.class || ''}`;
</script>

{#if href}
	<a {...$$restProps} {href} class={classes} class:disabled>
		{#if loading}
			<Loader2 class="w-4 h-4 loading-spinner" />
		{:else if icon}
			<svelte:component this={icon} class="w-4 h-4" />
		{/if}
		<slot />
	</a>
{:else}
	<button
		bind:this={el}
		{...$$restProps}
		{type}
		disabled={disabled || loading}
		aria-pressed={pressed}
		class={classes}
		on:click
		on:keydown
	>
		{#if loading}
			<Loader2 class="w-4 h-4 loading-spinner" />
		{:else if icon}
			<svelte:component this={icon} class="w-4 h-4" />
		{/if}
		<slot />
	</button>
{/if}
