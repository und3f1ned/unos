<script lang="ts">
	// The bar of an action on several containers (ADR-0389), a BulkBar (ADR-0432). It shows only
	// while containers are selected (ADR-0437): their count, Start, Stop and Restart selected, and
	// Clear; no disabled buttons wait above the list. It sits after the list and sticks to the foot
	// of the window while the list scrolls, so that it pushes no row from under the pointer. While an
	// action runs it says which container it is at, in place of the count.
	import { Play, RotateCw, Square } from 'lucide-svelte';
	import { BulkBar, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { BULK_PROGRESS, type BulkAction, type BulkProgress } from './bulk.svelte';

	type Props = {
		selectedCount: number;
		/** The run of an action on several containers, the container it is at; null when none runs. */
		progress: BulkProgress | null;
		/** An action on several containers runs. */
		busy: boolean;
		onclear: () => void;
		onbulk: (action: BulkAction) => void;
	};

	let { selectedCount, progress, busy, onclear, onbulk }: Props = $props();

	const ACTIONS: { id: BulkAction; Icon: typeof Play; key: string }[] = [
		{ id: 'start', Icon: Play, key: 'containers.bulk.start' },
		{ id: 'stop', Icon: Square, key: 'containers.bulk.stop' },
		{ id: 'restart', Icon: RotateCw, key: 'containers.bulk.restart' }
	];
</script>

{#if selectedCount > 0}
<BulkBar
	class="top-auto bottom-4"
	count={selectedCount}
	label={$t('containers.bulk.toolbar')}
	summary={progress
		? $t(BULK_PROGRESS[progress.action], { name: progress.name, i: progress.i, n: progress.n })
		: $t('containers.bulk.selected', { n: selectedCount })}
	onclear={busy ? null : onclear}
>
	{#each ACTIONS as action (action.id)}
		<Button variant="secondary" size="sm" icon={action.Icon} disabled={busy} onclick={() => onbulk(action.id)}>
			{$t(action.key)}
		</Button>
	{/each}
</BulkBar>
{/if}
