<script lang="ts">
	// The counts of the Containers page, each a button that filters the list by its state: a
	// second press of Running or Stopped shows all again.
	import { Box, CheckCircle2, XCircle } from 'lucide-svelte';
	import { Button, type IconComponent } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		running: number;
		stopped: number;
		total: number;
		/** The state the list is filtered by; '' is every state. */
		filter: string;
		onfilter: (state: string) => void;
	};

	let { running, stopped, total, filter, onfilter }: Props = $props();

	type Stat = {
		active: boolean;
		ring: string;
		next: string;
		label: string;
		count: number;
		countClass: string;
		iconBox: string;
		Icon: IconComponent;
		iconClass: string;
	};

	const [runningStat, stoppedStat, totalStat]: Stat[] = $derived([
		{
			active: filter === 'running',
			ring: 'ring-2 ring-status-success',
			next: filter === 'running' ? '' : 'running',
			label: $t('containers.filter.running'),
			count: running,
			countClass: 'text-2xl font-bold text-success',
			iconBox: 'w-10 h-10 rounded-full bg-status-success/20 flex items-center justify-center',
			Icon: CheckCircle2,
			iconClass: 'w-5 h-5 text-success'
		},
		{
			active: filter === 'exited',
			ring: 'ring-2 ring-status-error',
			next: filter === 'exited' ? '' : 'exited',
			label: $t('containers.filter.stopped'),
			count: stopped,
			countClass: 'text-2xl font-bold text-error',
			iconBox: 'w-10 h-10 rounded-full bg-status-error/20 flex items-center justify-center',
			Icon: XCircle,
			iconClass: 'w-5 h-5 text-error'
		},
		{
			active: filter === '',
			ring: 'ring-2 ring-brand-ring',
			next: '',
			label: $t('containers.filter.total'),
			count: total,
			countClass: 'text-2xl font-bold',
			iconBox: 'w-10 h-10 rounded-full bg-status-info/20 flex items-center justify-center',
			Icon: Box,
			iconClass: 'w-5 h-5 text-info'
		}
	]);
</script>

{#snippet card(stat: Stat)}
	<Button variant="plain"
	 class="card hover:bg-surface-overlay transition-colors text-left cursor-pointer {stat.active ? stat.ring : ''}"
		onclick={() => onfilter(stat.next)}
	>
		<div class="flex items-center justify-between">
			<div>
				<p class="text-secondary text-sm">{stat.label}</p>
				<p class={stat.countClass}>{stat.count}</p>
			</div>
			<div class={stat.iconBox}>
				<stat.Icon class={stat.iconClass} />
			</div>
		</div>
	</Button>
{/snippet}

<div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
	{@render card(runningStat)}
	{@render card(stoppedStat)}
	{@render card(totalStat)}
</div>
