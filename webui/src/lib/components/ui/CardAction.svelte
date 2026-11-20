<script lang="ts">
	import type { Snippet } from 'svelte';
	import { ArrowRight } from 'lucide-svelte';
	import Button from './Button.svelte';
	import type { IconComponent } from './types';

	// The action of a card's header (ADR-0432): with `href`, a text link with an arrow that leads to
	// the place where the thing is managed ("Users →"); without, a small secondary button that opens a
	// dialog or acts here. A card has no filled primary button: that is the page header's.
	type Props = {
		href?: string | null;
		icon?: IconComponent | null;
		disabled?: boolean;
		disabledReason?: string;
		loading?: boolean;
		onclick?: () => void;
		children: Snippet;
		[key: `aria-${string}`]: string | boolean | undefined;
	};

	let { href = null, icon = null, disabled = false, disabledReason = '', loading = false, onclick, children, ...rest }: Props =
		$props();
</script>

{#if href}
	<a {href} class="card-action-link" {...rest}>
		{@render children()}
		<ArrowRight class="w-3.5 h-3.5" aria-hidden="true" />
	</a>
{:else}
	<Button variant="secondary" size="sm" {icon} {disabled} {disabledReason} {loading} {onclick} {...rest}>
		{@render children()}
	</Button>
{/if}
