<script lang="ts">
	// A switch (ADR-0432): a setting that is on or off and takes effect as such, not an item to
	// select. A checkbox underneath with the role switch, so that the keyboard (Space), the
	// browser's forms and screen readers ("switch, on") work as with a checkbox. Without a visible
	// label it needs `label`, its name for screen readers.
	type Props = {
		checked?: boolean;
		id?: string;
		label?: string;
		/** The id of the element that names it, such as a SwitchRow's title. */
		labelledby?: string;
		describedby?: string;
		disabled?: boolean;
		name?: string;
		/** It was turned on or off, after `checked` took the new value. */
		onchange?: (checked: boolean) => void;
		class?: string;
	};

	let {
		checked = $bindable(false),
		id = undefined,
		label = '',
		labelledby = undefined,
		describedby = undefined,
		disabled = false,
		name = undefined,
		onchange,
		class: className = ''
	}: Props = $props();
</script>

<span class="switch {className}">
	<input
		{id}
		{name}
		type="checkbox"
		role="switch"
		bind:checked
		{disabled}
		aria-label={label || undefined}
		aria-labelledby={labelledby}
		aria-describedby={describedby}
		onchange={() => onchange?.(checked)}
	/>
	<span class="switch-track" aria-hidden="true"></span>
	<span class="switch-knob" aria-hidden="true"></span>
</span>
