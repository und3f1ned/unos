<script lang="ts">
	import { t } from '$lib/i18n';
	import type { AccessChoice } from './access';

	// The level of one row of a share's access table: None, Read, Read & write as one
	// segmented control of radios, so that every row shows its level and every choice at a glance and
	// the rows line up. A chosen level that gives access is drawn in the brand's tint, so that the
	// rows with access stand out from those without.

	type Props = {
		/** The radios' group name, unique in the form. */
		name: string;
		/** The level shown; null when none is (the All users row while the users' levels differ). */
		value: AccessChoice | null;
		/** The id of the element that names the row, or a label of its own. */
		labelledby?: string;
		label?: string;
		/** The id of the row's hint (its SMB state). */
		describedby?: string;
		/** Every level is disabled. */
		disabled?: boolean;
		/** The levels that cannot be chosen now (a share that can only lose access). */
		blocked?: AccessChoice[];
		onchange: (level: AccessChoice) => void;
	};

	let {
		name,
		value,
		labelledby = undefined,
		label = undefined,
		describedby = undefined,
		disabled = false,
		blocked = [],
		onchange
	}: Props = $props();

	const LEVELS: { level: AccessChoice; key: string }[] = [
		{ level: 'none', key: 'shares.access.none' },
		{ level: 'read', key: 'shares.access.read' },
		{ level: 'write', key: 'shares.access.write' }
	];
</script>

<div
	role="radiogroup"
	aria-labelledby={labelledby}
	aria-label={labelledby ? undefined : label}
	aria-describedby={describedby}
	class="segmented access-levels"
	class:access-levels-disabled={disabled}
>
	{#each LEVELS as choice (choice.level)}
		{@const off = disabled || blocked.includes(choice.level)}
		{@const chosen = value === choice.level}
		<label
			class="tab-pill access-level"
			class:tab-pill-active={chosen && choice.level === 'none'}
			class:access-level-granted={chosen && choice.level !== 'none'}
			class:access-level-off={off}
		>
			<input
				type="radio"
				class="access-radio"
				{name}
				value={choice.level}
				checked={chosen}
				disabled={off}
				onchange={() => onchange(choice.level)}
			/>
			{$t(choice.key)}
		</label>
	{/each}
</div>

<style>
	/* A phone: the three segments fill the row's width, each from the width of its words, so that
	   "Read & write" never runs out of its segment. From sm: as wide as their words, and the same in
	   every row, since every row has the same words. */
	.access-levels {
		@apply relative flex w-full flex-nowrap;
	}

	@media (min-width: 640px) {
		.access-levels {
			@apply inline-flex w-auto;
		}
	}

	.access-level {
		@apply relative flex-auto px-2 text-center whitespace-nowrap cursor-pointer select-none sm:flex-none sm:px-3;
	}

	/* The radio covers its segment, unseen, so that a click or a tap anywhere on it lands on the
	   radio itself; the segment takes the one focus ring of the app (ADR-0428). */
	.access-radio {
		@apply absolute inset-0 m-0 h-full w-full cursor-pointer appearance-none opacity-0;
	}

	.access-radio:disabled {
		@apply cursor-not-allowed;
	}

	/* The focused radio rings its segment, with the one focus ring of the app (ADR-0428). */
	.access-level:has(:focus-visible) {
		@apply outline outline-2 outline-offset-2 outline-brand-ring;
	}

	.access-level-granted {
		@apply bg-brand/20 text-content ring-1 ring-brand-ring hover:text-content;
	}

	.access-level-off {
		@apply cursor-not-allowed opacity-50 hover:text-content-secondary;
	}

	.access-level-off.access-level-granted,
	.access-level-off.tab-pill-active {
		@apply hover:text-content;
	}
</style>
