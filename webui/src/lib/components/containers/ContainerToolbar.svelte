<script lang="ts">
	// The search, the state filter and the choice of table or cards of the Containers page.
	import { LayoutGrid, List, Search } from 'lucide-svelte';
	import { Button, Card, Input } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		/** The text that a container's name or image contains. */
		search: string;
		/** The state the list is filtered by; '' is every state. */
		filter: string;
		view: 'table' | 'cards';
		onview: (view: 'table' | 'cards') => void;
	};

	let { search = $bindable(), filter = $bindable(), view, onview }: Props = $props();
</script>

<!-- The search with its icon, the state and the view as a segmented control (ADR-0428). -->
<Card padding="sm">
	<div class="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
		<Input
			icon={Search}
			placeholder={$t('containers.search')}
			ariaLabel={$t('containers.search')}
			bind:value={search}
			class="flex-1"
		/>
		<div class="flex flex-wrap items-center gap-2">
			<select bind:value={filter} class="form-select w-auto">
				<option value="">{$t('containers.filter.all')}</option>
				<option value="running">{$t('containers.filter.running')}</option>
				<option value="exited">{$t('containers.filter.stopped')}</option>
				<option value="paused">{$t('containers.filter.paused')}</option>
				<option value="restarting">{$t('containers.filter.restarting')}</option>
			</select>
			<div class="segmented">
				<Button variant="plain"
				 class="tab-pill {view === 'table' ? 'tab-pill-active' : ''}"
					pressed={view === 'table'}
					onclick={() => onview('table')}
					title={$t('containers.view.table')}
				>
					<List class="w-4 h-4" />
				</Button>
				<Button variant="plain"
				 class="tab-pill {view === 'cards' ? 'tab-pill-active' : ''}"
					pressed={view === 'cards'}
					onclick={() => onview('cards')}
					title={$t('containers.view.cards')}
				>
					<LayoutGrid class="w-4 h-4" />
				</Button>
			</div>
		</div>
	</div>
</Card>
