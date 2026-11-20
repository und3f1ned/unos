<script lang="ts">
	// The selection for an action on several containers (ADR-0389); it wraps on a phone.
	import { Play, RotateCw, Square } from 'lucide-svelte';
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { BulkAction } from './bulk.svelte';

	type Props = {
		/** Every container that the list shows is selected. */
		allSelected: boolean;
		/** The list shows none: nothing to select. */
		empty: boolean;
		selectedCount: number;
		/** An action on several containers runs. */
		busy: boolean;
		ontoggleall: (on: boolean) => void;
		onbulk: (action: BulkAction) => void;
	};

	let { allSelected, empty, selectedCount, busy, ontoggleall, onbulk }: Props = $props();
</script>

<div
	class="flex flex-wrap items-center gap-x-4 gap-y-2"
	role="toolbar"
	aria-label={$t('containers.bulk.toolbar')}
>
	<label class="flex items-center gap-2 text-sm">
		<input
			type="checkbox"
			class="form-checkbox"
			checked={allSelected}
			disabled={busy || empty}
			onchange={(event) => ontoggleall(event.currentTarget.checked)}
		/>
		{$t('containers.bulk.selectAll')}
	</label>
	<span class="text-sm text-content-secondary">
		{$t('containers.bulk.selected', { n: selectedCount })}
	</span>
	<div class="flex flex-wrap items-center gap-2">
		<Button
			variant="secondary"
			size="sm"
			icon={Play}
			disabled={busy || selectedCount === 0}
			onclick={() => onbulk('start')}>{$t('containers.bulk.start')}</Button
		>
		<Button
			variant="secondary"
			size="sm"
			icon={Square}
			disabled={busy || selectedCount === 0}
			onclick={() => onbulk('stop')}>{$t('containers.bulk.stop')}</Button
		>
		<Button
			variant="secondary"
			size="sm"
			icon={RotateCw}
			disabled={busy || selectedCount === 0}
			onclick={() => onbulk('restart')}>{$t('containers.bulk.restart')}</Button
		>
	</div>
</div>
