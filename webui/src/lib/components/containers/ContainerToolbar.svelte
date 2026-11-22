<script lang="ts">
	// The search, the state filter and the choice of table or cards of the Containers page. The
	// filter is one segmented control whose segments carry their counts, which add up to All: the
	// page's one place for the counts and the filter (webui.ux-apps). Other is shown only while a
	// container is in another state, or while it is the filter.
	import { LayoutGrid, List, Search } from 'lucide-svelte';
	import { Input, Segmented } from '$lib/components/ui';
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

<!-- One row from sm: the search grows, the filter and the view keep their width; on a phone the
     filter takes a row of its own under the search and the view, its segments sharing the row's
     width on one line, also with Other and in Russian (webui.ux-apps): each segment as wide as its
     name and count, and the rest of the row shared out, so that no name runs into the next. Counts
     of three digits that the row cannot hold wrap the filter onto a second line, never sideways. -->
<div class="flex flex-wrap items-center gap-3">
	<Input
		icon={Search}
		placeholder={$t('containers.search')}
		ariaLabel={$t('containers.search')}
		bind:value={search}
		class="flex-1 min-w-[12rem]"
	/>
	<!-- The filter: a segment's name and its count; the pressed one is the filter. -->
	<Segmented
		options={shown.map((segment) => ({ value: segment.id, label: $t(segment.key), count: counts[segment.id] }))}
		value={filter}
		label={$t('containers.filter.label')}
		onchange={(id) => (filter = id)}
		class="order-last w-full sm:order-none sm:w-auto"
		itemClass="flex-auto justify-center whitespace-nowrap px-1.5 sm:flex-none sm:px-3"
	/>
	<Segmented
		options={[
			{ value: 'table', label: $t('containers.view.table'), icon: List, iconOnly: true },
			{ value: 'cards', label: $t('containers.view.cards'), icon: LayoutGrid, iconOnly: true }
		]}
		value={view}
		label={$t('containers.view.label')}
		onchange={onview}
	/>
</div>
