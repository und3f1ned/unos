<script lang="ts">
	import type { Snippet } from 'svelte';

	// A checkbox of the theme (ADR-0432): an item to select, or a choice of a form that takes effect
	// when the form is saved; a setting that acts at once is a Switch. A native checkbox drawn in the
	// tokens (18 px), with its label and an optional line under it; `indeterminate` for "some of
	// them". `group` binds a list of the values checked, as bind:group does.
	type Props = {
		checked?: boolean;
		group?: string[] | undefined;
		value?: string;
		label?: string;
		description?: string;
		/** The name for screen readers where no label shows, such as a row's checkbox. */
		ariaLabel?: string;
		/** The id of the element that describes it, such as a HelpText under it. */
		describedby?: string;
		indeterminate?: boolean;
		disabled?: boolean;
		id?: string;
		name?: string;
		onchange?: (checked: boolean) => void;
		class?: string;
		children?: Snippet;
	};

	let {
		checked = $bindable(false),
		group = $bindable(undefined),
		value = '',
		label = '',
		description = '',
		ariaLabel = '',
		describedby = undefined,
		indeterminate = false,
		disabled = false,
		id = undefined,
		name = undefined,
		onchange,
		class: className = '',
		children
	}: Props = $props();

	const inGroup = $derived(group !== undefined);
	const isChecked = $derived(inGroup ? (group ?? []).includes(value) : checked);

	function change(event: Event & { currentTarget: HTMLInputElement }) {
		const on = event.currentTarget.checked;
		if (inGroup) {
			const list = group ?? [];
			group = on ? [...list.filter((item) => item !== value), value] : list.filter((item) => item !== value);
		} else {
			checked = on;
		}
		onchange?.(on);
	}
</script>

{#snippet box()}
	<input
		{id}
		{name}
		{value}
		type="checkbox"
		class="checkbox"
		checked={isChecked}
		{indeterminate}
		{disabled}
		aria-label={ariaLabel || undefined}
		aria-describedby={describedby}
		onchange={change}
	/>
{/snippet}

{#if label || children}
	<label class="choice {disabled ? 'choice-disabled' : ''} {className}">
		{@render box()}
		<span class="min-w-0">
			<span class="block">{#if children}{@render children()}{:else}{label}{/if}</span>
			{#if description}<span class="choice-description">{description}</span>{/if}
		</span>
	</label>
{:else}
	<span class="inline-flex {className}">{@render box()}</span>
{/if}
