<script lang="ts">
	// The counts of the Containers page. The list is filtered by the toolbar's state select alone:
	// the counts are no second filter (ADR-0431).
	import { Box, CheckCircle2, XCircle } from 'lucide-svelte';
	import { Card, type IconComponent } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		running: number;
		stopped: number;
		total: number;
	};

	let { running, stopped, total }: Props = $props();

	type Stat = { label: string; count: number; iconBox: string; Icon: IconComponent; iconClass: string };

	const stats: Stat[] = $derived([
		{
			label: $t('containers.filter.running'),
			count: running,
			iconBox: 'bg-status-success/20',
			Icon: CheckCircle2,
			iconClass: 'text-success'
		},
		{
			label: $t('containers.filter.stopped'),
			count: stopped,
			iconBox: 'bg-status-error/20',
			Icon: XCircle,
			iconClass: 'text-error'
		},
		{
			label: $t('containers.filter.total'),
			count: total,
			iconBox: 'bg-status-neutral/20',
			Icon: Box,
			iconClass: 'text-content-secondary'
		}
	]);
</script>

<!-- A compact stat (ADR-0428): the label over the count. -->
<div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
	{#each stats as stat (stat.label)}
		<Card padding="sm">
			<div class="flex items-center gap-3">
				<div class="w-8 h-8 rounded-full flex items-center justify-center {stat.iconBox}" aria-hidden="true">
					<stat.Icon class="w-4 h-4 {stat.iconClass}" />
				</div>
				<div class="min-w-0">
					<p class="text-secondary text-xs">{stat.label}</p>
					<p class="text-xl font-semibold tabular">{stat.count}</p>
				</div>
			</div>
		</Card>
	{/each}
</div>
