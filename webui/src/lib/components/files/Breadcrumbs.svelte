<script lang="ts">
	import { ChevronRight } from 'lucide-svelte';
	import { Button } from '$lib/components/ui';
	import type { Crumb } from './paths';

	type Props = {
		// From the pools directory down to the folder that is open, the last one.
		crumbs: Crumb[];
		// A crumb was chosen: open its folder.
		onnavigate: (path: string) => void;
	};

	let { crumbs, onnavigate }: Props = $props();
</script>

<!-- Breadcrumbs: a bar of 28 px crumbs, the folder that is open in the text's colour (ADR-0428). -->
<div
	class="flex items-center gap-0.5 mb-4 px-1.5 py-1 text-sm overflow-x-auto rounded-lg border border-border bg-surface-elevated"
>
	{#each crumbs as crumb, i}
		{#if i > 0}
			<ChevronRight class="w-4 h-4 text-content-muted flex-shrink-0" />
		{/if}
		<Button
			variant="plain"
			onclick={() => onnavigate(crumb.path)}
			class="h-7 px-2 rounded hover:bg-surface-overlay truncate {i === crumbs.length - 1
				? 'text-content font-medium'
				: 'text-content-secondary'}"
		>
			{crumb.name}
		</Button>
	{/each}
</div>
