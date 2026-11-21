<script lang="ts">
	import { RadioGroup } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { AccessChoice } from './access';

	// The level of one row of a share's access table: None, Read, Read & write as one segmented
	// RadioGroup, so that every row shows its level and every choice at a glance and the rows line
	// up. A chosen level that gives access is drawn in the brand's tint, so that the rows with
	// access stand out from those without.

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
		label = '',
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

	const options = $derived(
		LEVELS.map((choice) => ({
			value: choice.level,
			label: $t(choice.key),
			disabled: blocked.includes(choice.level),
			emphasis: choice.level !== 'none'
		}))
	);
</script>

<RadioGroup
	segmented
	{name}
	value={value ?? ''}
	{options}
	legend={label}
	{labelledby}
	{describedby}
	{disabled}
	onchange={(level) => onchange(level as AccessChoice)}
/>
