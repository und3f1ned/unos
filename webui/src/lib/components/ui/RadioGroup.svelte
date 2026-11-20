<script lang="ts">
	import type { IconComponent } from './types';

	// One choice of several (ADR-0432): a fieldset with its legend and native radio buttons drawn in
	// the tokens, each with its label, an optional icon and an optional line under it. The arrow keys
	// move the choice, as the browser does for radio buttons of one name. `inline` lays short options
	// on one row, wrapping; else one under another. `description` is one line of help under the
	// options, which describes the group for screen readers.
	type Option = { value: string; label: string; description?: string; icon?: IconComponent | null; disabled?: boolean };
	type Props = {
		value?: string;
		options: readonly Option[];
		/** The question the choice answers; `legendHidden` keeps it for screen readers only. */
		legend: string;
		legendHidden?: boolean;
		description?: string;
		name?: string;
		inline?: boolean;
		disabled?: boolean;
		onchange?: (value: string) => void;
		class?: string;
	};

	const uid = Math.random().toString(36).slice(2, 9);

	let {
		value = $bindable(''),
		options,
		legend,
		legendHidden = false,
		description = '',
		name = `radio-${uid}`,
		inline = false,
		disabled = false,
		onchange,
		class: className = ''
	}: Props = $props();

	const descriptionId = `radio-help-${uid}`;
</script>

<fieldset class="field-group {className}" aria-describedby={description ? descriptionId : undefined}>
	<legend class={legendHidden ? 'sr-only' : 'form-label'}>{legend}</legend>
	<div class={inline ? 'flex flex-wrap gap-x-6 gap-y-2' : 'space-y-2.5'}>
		{#each options as option (option.value)}
			{@const Icon = option.icon}
			<label class="choice {disabled || option.disabled ? 'choice-disabled' : ''}">
				<input
					type="radio"
					class="radio"
					{name}
					value={option.value}
					checked={value === option.value}
					disabled={disabled || option.disabled}
					onchange={() => {
						value = option.value;
						onchange?.(option.value);
					}}
				/>
				{#if Icon}<Icon class="w-4 h-4 mt-0.5 shrink-0 text-content-secondary" aria-hidden="true" />{/if}
				<span class="min-w-0">
					<span class="block">{option.label}</span>
					{#if option.description}<span class="choice-description">{option.description}</span>{/if}
				</span>
			</label>
		{/each}
	</div>
	{#if description}
		<p id={descriptionId} class="form-hint">{description}</p>
	{/if}
</fieldset>
