<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAnchorAttributes, HTMLButtonAttributes } from 'svelte/elements';
	import { Loader2 } from 'lucide-svelte';
	import type { IconComponent } from './types';
	import { placeTip } from './float';

	// The attributes of a button, and of a link for one with `href` (target, rel).
	type Props = Omit<HTMLButtonAttributes, 'type'> & Pick<HTMLAnchorAttributes, 'target' | 'rel'> & {
		// `plain` brings no look of its own: a control that styles itself through `class` (a tab, a
		// clickable card or row, the classes `tab-*` of app.css). `ghost-danger`: an icon button that
		// deletes or removes (ADR-0428).
		variant?: 'primary' | 'secondary' | 'danger' | 'warning' | 'success' | 'ghost' | 'ghost-danger' | 'plain';
		size?: 'sm' | 'md' | 'lg' | 'icon' | 'icon-sm';
		type?: 'button' | 'submit' | 'reset';
		disabled?: boolean;
		/**
		 * Why the button is disabled, in a few words (ADR-0432): the button stays focusable
		 * (aria-disabled instead of disabled), a click does nothing, and the reason shows as a
		 * tooltip on hover and focus and is its accessible description.
		 */
		disabledReason?: string;
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
		disabledReason = '',
		loading = false,
		icon = null,
		href = null,
		pressed = undefined,
		el = $bindable(null),
		class: className = '',
		onclick,
		onkeydown,
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

	// A disabled button with its reason stays in the Tab order, so that the reason can be read.
	const explained = $derived(disabled && !loading && disabledReason !== '');
	const tipId = `btn-tip-${Math.random().toString(36).slice(2, 9)}`;
	let tip: HTMLSpanElement | null = $state(null);
	let tipShown = $state(false);

	function showTip() {
		if (!explained || !el || !tip) return;
		tipShown = true;
		// Placed once it is drawn: its size is known then.
		requestAnimationFrame(() => {
			if (el && tip) placeTip(tip, el.getBoundingClientRect());
		});
	}

	function hideTip() {
		tipShown = false;
	}

	function handleClick(event: MouseEvent & { currentTarget: EventTarget & HTMLButtonElement }) {
		if (explained) {
			// Neither the page's handler nor a form's submit.
			event.preventDefault();
			showTip();
			return;
		}
		onclick?.(event);
	}

	function handleKeydown(event: KeyboardEvent & { currentTarget: EventTarget & HTMLButtonElement }) {
		if (event.key === 'Escape' && tipShown) {
			tipShown = false;
			event.preventDefault();
			return;
		}
		onkeydown?.(event);
	}
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
	<a {...rest as Record<string, unknown>} {href} class={classes} class:disabled onclick={onclick as HTMLAnchorAttributes['onclick']}>
		{@render content()}
	</a>
{:else}
	<button
		bind:this={el}
		{...rest}
		{type}
		disabled={explained ? undefined : disabled || loading}
		aria-disabled={explained ? 'true' : undefined}
		aria-describedby={explained ? [tipId, rest['aria-describedby']].filter(Boolean).join(' ') : rest['aria-describedby']}
		aria-pressed={pressed}
		aria-busy={loading || undefined}
		class={classes}
		onclick={handleClick}
		onkeydown={handleKeydown}
		onpointerenter={(event) => {
			showTip();
			rest.onpointerenter?.(event);
		}}
		onpointerleave={(event) => {
			hideTip();
			rest.onpointerleave?.(event);
		}}
		onfocus={(event) => {
			showTip();
			rest.onfocus?.(event);
		}}
		onblur={(event) => {
			hideTip();
			rest.onblur?.(event);
		}}
	>
		{@render content()}
	</button>
	{#if explained}
		<!-- The reason: the button's description for screen readers (also while hidden), and a
		     tooltip in the top layer of the page while the pointer or the focus is on it. -->
		<span bind:this={tip} id={tipId} role="tooltip" class="tooltip" hidden={!tipShown}>{disabledReason}</span>
	{/if}
{/if}
