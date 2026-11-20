<script lang="ts">
	import type { Snippet } from 'svelte';
	import Switch from './Switch.svelte';

	// A setting turned on and off (ADR-0432): its title and one line of help on the left, the switch
	// on the right, and below, while it is on, the fields the setting needs, in one column. Nothing
	// of a setting that is off shows: neither its fields nor its reasons.
	type Props = {
		title: string;
		help?: string;
		checked?: boolean;
		disabled?: boolean;
		id?: string;
		onchange?: (checked: boolean) => void;
		class?: string;
		/** What the setting needs while it is on. */
		children?: Snippet;
	};

	let {
		title,
		help = '',
		checked = $bindable(false),
		disabled = false,
		id = `switch-${Math.random().toString(36).slice(2, 9)}`,
		onchange,
		class: className = '',
		children
	}: Props = $props();

	const titleId = $derived(`${id}-title`);
	const helpId = $derived(`${id}-help`);
</script>

<div class={className}>
	<div class="switch-row">
		<label for={id} class="min-w-0 cursor-pointer">
			<span id={titleId} class="switch-row-title">{title}</span>
			{#if help}<span id={helpId} class="switch-row-help">{help}</span>{/if}
		</label>
		<Switch {id} bind:checked {disabled} labelledby={titleId} describedby={help ? helpId : undefined} {onchange} />
	</div>
	{#if checked && children}
		<div class="mt-3 space-y-4">
			{@render children()}
		</div>
	{/if}
</div>
