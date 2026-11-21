<script lang="ts" generics="T extends string | number">
	import type { IconComponent } from './types';
	import Button from './Button.svelte';

	// A segmented control (ADR-0432): a filter or a view of what the page shows, one of a few, as
	// toggle buttons in a group (aria-pressed), the chosen one lifted. Page sections are Tabs. An
	// option's `count` follows its label, muted: a filter whose segments say how many each holds.
	type Option = { value: T; label: string; icon?: IconComponent | null; iconOnly?: boolean; count?: number };
	type Props = {
		options: readonly Option[];
		value: T;
		/** The group's name for screen readers. */
		label: string;
		onchange: (value: T) => void;
		class?: string;
		/** Classes of each segment, such as a filter that shares a phone's row on one line. */
		itemClass?: string;
	};

	let { options, value, label, onchange, class: className = '', itemClass = '' }: Props = $props();
</script>

<div class="segmented {className}" role="group" aria-label={label}>
	{#each options as option (option.value)}
		<Button
			variant="plain"
			class="tab-pill {value === option.value ? 'tab-pill-active' : ''} {option.icon || option.count !== undefined ? 'inline-flex items-center gap-1.5' : ''} {itemClass}"
			pressed={value === option.value}
			aria-label={option.iconOnly ? option.label : undefined}
			title={option.iconOnly ? option.label : undefined}
			onclick={() => onchange(option.value)}
		>
			{#if option.icon}
				{@const Icon = option.icon}
				<Icon class="w-4 h-4" aria-hidden="true" />
			{/if}
			{#if !option.iconOnly}{option.label}{/if}
			{#if option.count !== undefined}<span class="tabular text-content-muted" data-count>{option.count}</span>{/if}
		</Button>
	{/each}
</div>
