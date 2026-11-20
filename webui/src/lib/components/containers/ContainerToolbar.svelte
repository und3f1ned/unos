<script lang="ts">
	// The search, the state filter and the choice of table or cards of the Containers page. The
	// filter is one segmented control whose segments carry their counts, which add up to All: the
	// page's one place for the counts and the filter (webui.ux-apps). Other is shown only while a
	// container is in another state, or while it is the filter.
	import { LayoutGrid, List, Search } from 'lucide-svelte';
	import { Button, Input } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { StateFilter } from './labels';

	type Props = {
		/** The text that a container's name or image contains. */
		search: string;
		filter: StateFilter;
		counts: Record<StateFilter, number>;
		view: 'table' | 'cards';
		onview: (view: 'table' | 'cards') => void;
	};

	let { search = $bindable(), filter = $bindable(), counts, view, onview }: Props = $props();

	const SEGMENTS: { id: StateFilter; key: string }[] = [
		{ id: 'all', key: 'containers.filter.all' },
		{ id: 'running', key: 'containers.filter.running' },
		{ id: 'stopped', key: 'containers.filter.stopped' },
		{ id: 'other', key: 'containers.filter.other' }
	];
	const shown = $derived(SEGMENTS.filter((s) => s.id !== 'other' || counts.other > 0 || filter === 'other'));
</script>

{#snippet viewButton(id: 'table' | 'cards', Icon: typeof List, key: string)}
	<Button
		variant="plain"
		class="tab-pill {view === id ? 'tab-pill-active' : ''}"
		pressed={view === id}
		onclick={() => onview(id)}
		title={$t(key)}
	>
		<Icon class="w-4 h-4" aria-hidden="true" />
		<span class="sr-only">{$t(key)}</span>
	</Button>
{/snippet}

<!-- One row from sm: the search grows, the filter and the view keep their width; on a phone the
     filter takes a row of its own under the search and the view. -->
<div class="flex flex-wrap items-center gap-3">
	<Input
		icon={Search}
		placeholder={$t('containers.search')}
		ariaLabel={$t('containers.search')}
		bind:value={search}
		class="flex-1 min-w-[12rem]"
	/>
	<!-- The filter: a segment's name and its count; the pressed one is the filter. -->
	<div
		class="segmented order-last w-full sm:order-none sm:w-auto"
		role="group"
		aria-label={$t('containers.filter.label')}
	>
		{#each shown as segment (segment.id)}
			<Button
				variant="plain"
				class="tab-pill inline-flex items-center gap-1.5 {filter === segment.id ? 'tab-pill-active' : ''}"
				pressed={filter === segment.id}
				onclick={() => (filter = segment.id)}
			>
				{$t(segment.key)}
				<span class="tabular text-content-muted" data-count>{counts[segment.id]}</span>
			</Button>
		{/each}
	</div>
	<div class="segmented" role="group" aria-label={$t('containers.view.label')}>
		{@render viewButton('table', List, 'containers.view.table')}
		{@render viewButton('cards', LayoutGrid, 'containers.view.cards')}
	</div>
</div>
