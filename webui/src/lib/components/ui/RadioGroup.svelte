<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { IconComponent } from './types';

	// One choice of several (ADR-0432): a fieldset with its legend and native radio buttons drawn in
	// the tokens, each with its label, an optional icon and an optional line under it. The arrow keys
	// move the choice, as the browser does for radio buttons of one name. `inline` lays short options
	// on one row, wrapping; else one under another. `description` is one line of help under the
	// options, which describes the group for screen readers.
	//
	// `segmented` draws the options as one segmented control in a radiogroup that something else
	// names (`labelledby`, such as a row's name in a table of rows that each take a choice), or the
	// legend for screen readers: a short choice that every row shows at a glance. Its value may be
	// none of the options (''), and an option with `emphasis` is drawn in the brand's tint while
	// chosen (a level that grants something), so that such rows stand out.
	//
	// `item` draws an option's text in place of its label and description, for an option that says
	// more than one line can, such as a disk with its size, model and state.
	type Option = {
		value: string;
		label: string;
		description?: string;
		icon?: IconComponent | null;
		disabled?: boolean;
		/** segmented: drawn in the brand's tint while chosen. */
		emphasis?: boolean;
	};
	type Props = {
		value?: string;
		options: readonly Option[];
		/** The question the choice answers; `legendHidden` keeps it for screen readers only. */
		legend: string;
		legendHidden?: boolean;
		description?: string;
		name?: string;
		inline?: boolean;
		/** One segmented control (above). */
		segmented?: boolean;
		/** segmented: the id of the element that names the group, in place of the legend. */
		labelledby?: string;
		/** segmented: the id of the element that describes the group. */
		describedby?: string;
		disabled?: boolean;
		onchange?: (value: string) => void;
		/** The text of an option, in place of its label and description (not segmented). */
		item?: Snippet<[Option]>;
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
		segmented = false,
		labelledby = undefined,
		describedby = undefined,
		disabled = false,
		onchange,
		item,
		class: className = ''
	}: Props = $props();

	const descriptionId = `radio-help-${uid}`;

	function choose(option: Option) {
		value = option.value;
		onchange?.(option.value);
	}
</script>

{#if segmented}
	<div
		role="radiogroup"
		aria-labelledby={labelledby}
		aria-label={labelledby ? undefined : legend}
		aria-describedby={describedby}
		class="segmented radio-segments {className}"
	>
		{#each options as option (option.value)}
			{@const off = disabled || option.disabled}
			{@const chosen = value === option.value}
			<label
				class="tab-pill radio-segment"
				class:tab-pill-active={chosen && !option.emphasis}
				class:radio-segment-emphasis={chosen && option.emphasis}
				class:radio-segment-off={off}
			>
				<input
					type="radio"
					class="radio-segment-input"
					{name}
					value={option.value}
					checked={chosen}
					disabled={off}
					onchange={() => choose(option)}
				/>
				{option.label}
			</label>
		{/each}
	</div>
{:else}
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
						onchange={() => choose(option)}
					/>
					{#if Icon}<Icon class="w-4 h-4 mt-0.5 shrink-0 text-content-secondary" aria-hidden="true" />{/if}
					<span class="min-w-0 {item ? 'flex-1' : ''}">
						{#if item}
							{@render item(option)}
						{:else}
							<span class="block">{option.label}</span>
							{#if option.description}<span class="choice-description">{option.description}</span>{/if}
						{/if}
					</span>
				</label>
			{/each}
		</div>
		{#if description}
			<p id={descriptionId} class="form-hint">{description}</p>
		{/if}
	</fieldset>
{/if}

<style>
	/* The utilities and tokens of app.css for @apply (Tailwind CSS 4, ADR-0450). */
	@reference '../../../app.css';

	/* segmented. A phone: the segments fill the row's width, each from the width of its words, so
	   that a long label never runs out of its segment. From sm: as wide as their words. */
	.radio-segments {
		@apply relative flex w-full flex-nowrap;
	}

	@media (min-width: 640px) {
		.radio-segments {
			@apply inline-flex w-auto;
		}
	}

	.radio-segment {
		@apply relative flex-auto px-2 text-center whitespace-nowrap cursor-pointer select-none sm:flex-none sm:px-3;
	}

	/* The radio covers its segment, unseen, so that a click or a tap anywhere on it lands on the
	   radio itself; the segment takes the one focus ring of the app (ADR-0428). */
	.radio-segment-input {
		@apply absolute inset-0 m-0 h-full w-full cursor-pointer appearance-none opacity-0;
	}

	.radio-segment-input:disabled {
		@apply cursor-not-allowed;
	}

	.radio-segment:has(:focus-visible) {
		@apply outline outline-2 outline-offset-2 outline-brand-ring;
	}

	.radio-segment-emphasis {
		@apply bg-brand/20 text-content ring-1 ring-brand-ring hover:text-content;
	}

	.radio-segment-off {
		@apply cursor-not-allowed opacity-50 hover:text-content-secondary;
	}

	.radio-segment-off.radio-segment-emphasis,
	.radio-segment-off.tab-pill-active {
		@apply hover:text-content;
	}
</style>
