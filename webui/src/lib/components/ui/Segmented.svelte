<script lang="ts" generics="T extends string | number">
	import type { IconComponent } from './types';
	import Button from './Button.svelte';

	// A segmented control (ADR-0432): a filter or a view of what the page shows, one of a few, as
	// toggle buttons in a group (aria-pressed), the chosen one lifted. Page sections are Tabs.
	type Option = { value: T; label: string; icon?: IconComponent | null; iconOnly?: boolean };
	type Props = {
		options: readonly Option[];
		value: T;
		/** The group's name for screen readers. */
		label: string;
		onchange: (value: T) => void;
		class?: string;
	};

	let { options, value, label, onchange, class: className = '' }: Props = $props();
</script>

<div class="segmented {className}" role="group" aria-label={label}>
	{#each options as option (option.value)}
		<Button
			variant="plain"
			class="tab-pill {value === option.value ? 'tab-pill-active' : ''} {option.icon ? 'inline-flex items-center gap-1.5' : ''}"
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
		</Button>
	{/each}
</div>
