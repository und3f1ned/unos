<script lang="ts">
	import { ArrowDown, ArrowUp, EyeOff, GripVertical } from 'lucide-svelte';
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The tools of a widget while the widgets are arranged (ADR-0395): its drag handle, Move up,
	// Move down and Hide. The page finds the buttons by data-toolbar to keep the focus on them after
	// a move.

	type Props = {
		/** The widget's name, for the buttons' texts. */
		name: string;
		/** The widget is the first, or the last, of those shown. */
		first: boolean;
		last: boolean;
		saving: boolean;
		ondragstart: (event: DragEvent) => void;
		ondragend: () => void;
		/** Move up (-1) or Move down (1) was pressed. */
		onmove: (direction: -1 | 1) => void;
		onhide: () => void;
	};

	let { name, first, last, saving, ondragstart, ondragend, onmove, onhide }: Props = $props();
</script>

<!-- A small bar in the widget's top right corner while the widgets are arranged (ADR-0428). -->
<div
	data-toolbar
	class="absolute -top-3 right-2 z-[2] flex items-center gap-0.5 p-0.5 rounded-lg border border-border bg-surface-elevated shadow-popover"
>
	<!-- Dragging is for a mouse; the buttons are for the keyboard and a finger. -->
	<span
		data-drag-handle
		draggable="true"
		role="presentation"
		title={$t('dashboard.layout.drag')}
		class="p-1.5 cursor-grab text-secondary rounded hover:bg-surface-overlay"
		{ondragstart}
		{ondragend}
	>
		<GripVertical class="w-4 h-4" />
	</span>
	<Button variant="ghost" size="icon-sm" disabled={saving || first} onclick={() => onmove(-1)}>
		<ArrowUp class="w-4 h-4" />
		<span class="sr-only">{$t('dashboard.layout.moveUp', { name })}</span>
	</Button>
	<Button variant="ghost" size="icon-sm" disabled={saving || last} onclick={() => onmove(1)}>
		<ArrowDown class="w-4 h-4" />
		<span class="sr-only">{$t('dashboard.layout.moveDown', { name })}</span>
	</Button>
	<Button variant="ghost" size="icon-sm" disabled={saving} onclick={onhide}>
		<EyeOff class="w-4 h-4" />
		<span class="sr-only">{$t('dashboard.layout.hide', { name })}</span>
	</Button>
</div>
