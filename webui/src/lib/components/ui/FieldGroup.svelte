<script lang="ts">
	import type { Snippet } from 'svelte';

	// Fields that belong together (ADR-0432), such as the snapshots a retention keeps: a fieldset with
	// its legend and an optional line of help, the fields in columns of at least `minWidth` on a
	// subgrid, so that a row's labels end on one line, its fields line up and its hints follow,
	// whatever the length of each label. Each child is one field (Input, Select) or a div of a
	// label, a field and a hint.
	type Props = {
		legend: string;
		description?: string;
		/** The narrowest column, as CSS: 9rem by default. */
		minWidth?: string;
		class?: string;
		children: Snippet;
	};

	let { legend, description = '', minWidth = '9rem', class: className = '', children }: Props = $props();
	const descriptionId = `field-group-${Math.random().toString(36).slice(2, 9)}`;
</script>

<fieldset class="field-group {className}" aria-describedby={description ? descriptionId : undefined}>
	<legend class="field-group-legend">{legend}</legend>
	{#if description}
		<p id={descriptionId} class="field-group-description">{description}</p>
	{/if}
	<div class="field-group-grid" style:--field-min={minWidth}>
		{@render children()}
	</div>
</fieldset>
