<script lang="ts">
	// The bar of an action on several containers (ADR-0389). It shows only while containers are
	// selected (webui.ux-apps): their count, Start, Stop and Restart selected, and Clear; no
	// disabled buttons wait above the list. It sits after the list and sticks to the bottom of the
	// window while the list scrolls, so that it pushes no row from under the pointer; it wraps on a
	// phone.
	import { Play, RotateCw, Square, X } from 'lucide-svelte';
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { BulkAction } from './bulk.svelte';

	type Props = {
		selectedCount: number;
		/** An action on several containers runs. */
		busy: boolean;
		onclear: () => void;
		onbulk: (action: BulkAction) => void;
	};

	let { selectedCount, busy, onclear, onbulk }: Props = $props();

	const ACTIONS: { id: BulkAction; Icon: typeof Play; key: string }[] = [
		{ id: 'start', Icon: Play, key: 'containers.bulk.start' },
		{ id: 'stop', Icon: Square, key: 'containers.bulk.stop' },
		{ id: 'restart', Icon: RotateCw, key: 'containers.bulk.restart' }
	];
</script>

{#if selectedCount > 0}
	<div
		class="sticky bottom-4 z-10 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-card border border-border-strong bg-surface-elevated px-4 py-2.5 shadow-popover"
		role="toolbar"
		aria-label={$t('containers.bulk.toolbar')}
	>
		<span class="text-sm font-medium" aria-live="polite">
			{$t('containers.bulk.selected', { n: selectedCount })}
		</span>
		<div class="flex flex-wrap items-center gap-2 sm:ml-auto">
			{#each ACTIONS as action (action.id)}
				<Button
					variant="secondary"
					size="sm"
					icon={action.Icon}
					disabled={busy}
					onclick={() => onbulk(action.id)}>{$t(action.key)}</Button
				>
			{/each}
			<Button variant="ghost" size="sm" icon={X} disabled={busy} onclick={onclear}>
				{$t('containers.bulk.clear')}
			</Button>
		</div>
	</div>
{/if}
