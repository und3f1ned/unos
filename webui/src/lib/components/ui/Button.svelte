<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAnchorAttributes, HTMLButtonAttributes } from 'svelte/elements';
	import { Loader2 } from 'lucide-svelte';
	import type { IconComponent } from './types';

	// The attributes of a button, and of a link for one with `href` (target, rel).
	type Props = Omit<HTMLButtonAttributes, 'type'> & Pick<HTMLAnchorAttributes, 'target' | 'rel'> & {
		// `plain` brings no look of its own: a control that styles itself through `class` (a tab, a
		// clickable card or row, the classes `tab-*` of app.css). `ghost-danger`: an icon button that
		// deletes or removes (ADR-0428).
		variant?: 'primary' | 'secondary' | 'danger' | 'warning' | 'success' | 'ghost' | 'ghost-danger' | 'plain';
		size?: 'sm' | 'md' | 'lg' | 'icon' | 'icon-sm';
		type?: 'button' | 'submit' | 'reset';
		disabled?: boolean;
		loading?: boolean;
		icon?: IconComponent | null;
		href?: string | null;
		// A toggle button (aria-pressed): true while what it shows or turns on is active.
		pressed?: boolean | undefined;
		// The button element, for a page that moves the focus or measures it: bind:el.
		el?: HTMLButtonElement | null;
		class?: string;
		children?: Snippet;
	};

	let {
		variant = 'primary',
		size = 'md',
		type = 'button',
		disabled = false,
		loading = false,
		icon = null,
		href = null,
		pressed = undefined,
		el = $bindable(null),
		class: className = '',
		children,
		...rest
	}: Props = $props();

	const variantClasses = {
		primary: 'btn-primary',
		secondary: 'btn-secondary',
		danger: 'btn-danger',
		warning: 'btn-warning',
		success: 'btn-success',
		ghost: 'btn-ghost',
		'ghost-danger': 'btn-ghost-danger',
		plain: ''
	};

	const sizeClasses = {
		sm: 'btn-sm',
		md: 'btn-md',
		lg: 'btn-lg',
		icon: 'btn-icon',
		'icon-sm': 'btn-icon-sm'
	};

	const classes = $derived(
		variant === 'plain'
			? `${className || ''}`
			: `btn ${variantClasses[variant]} ${sizeClasses[size]} ${className || ''}`
	);
</script>

{#snippet content()}
	{#if loading}
		<Loader2 class="w-4 h-4 loading-spinner" aria-hidden="true" />
	{:else if icon}
		{@const Icon = icon}
		<Icon class="w-4 h-4 shrink-0" aria-hidden="true" />
	{/if}
	{@render children?.()}
{/snippet}

{#if href}
	<a {...rest as Record<string, unknown>} {href} class={classes} class:disabled>
		{@render content()}
	</a>
{:else}
	<button
		bind:this={el}
		{...rest}
		{type}
		disabled={disabled || loading}
		aria-pressed={pressed}
		aria-busy={loading || undefined}
		class={classes}
	>
		{@render content()}
	</button>
{/if}
