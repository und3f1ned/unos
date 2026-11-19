<script lang="ts">
	// The search, the state filter and the choice of table or cards of the Containers page.
	import { LayoutGrid, List, Search } from 'lucide-svelte';
	import { Button, Card } from '$lib/components/ui';
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

<!-- Search and View Toggle -->
<Card>
	<div class="flex flex-col sm:flex-row items-stretch sm:items-center gap-4">
		<div class="relative flex-1">
			<Search
				class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-content-muted pointer-events-none"
			/>
			<input
				type="text"
				placeholder={$t('containers.search')}
				bind:value={search}
				class="form-input pl-10"
			/>
		</div>
		<div class="flex items-center gap-2">
			<select bind:value={filter} class="form-select w-auto">
				<option value="">{$t('containers.filter.all')}</option>
				<option value="running">{$t('containers.filter.running')}</option>
				<option value="exited">{$t('containers.filter.stopped')}</option>
				<option value="paused">{$t('containers.filter.paused')}</option>
				<option value="restarting">{$t('containers.filter.restarting')}</option>
			</select>
			<div class="flex border border-border rounded-lg overflow-hidden">
				<Button variant="plain"
				 class="p-2 {view === 'table' ? 'bg-surface-overlay' : 'bg-transparent'}"
					onclick={() => onview('table')}
					title={$t('containers.view.table')}
				>
					<List class="w-4 h-4" />
				</Button>
				<Button variant="plain"
				 class="p-2 {view === 'cards' ? 'bg-surface-overlay' : 'bg-transparent'}"
					onclick={() => onview('cards')}
					title={$t('containers.view.cards')}
				>
					<LayoutGrid class="w-4 h-4" />
				</Button>
			</div>
		</div>
	</div>
</Card>
