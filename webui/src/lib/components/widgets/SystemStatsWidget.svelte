<script lang="ts">
	import { Cpu, HardDrive, MemoryStick, Network, Thermometer } from 'lucide-svelte';
	import { Card, ProgressBar } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { isAdmin } from '$lib/stores/auth';
	import { formatBytes } from './bytes';

	type Props = {
		cpuPercent?: number;
		memoryPercent?: number;
		memoryUsed?: number;
		memoryTotal?: number;
		networkUp?: number;
		networkDown?: number;
		// The bytes per second read from and written to the host's disks (ADR-0388).
		diskRead?: number;
		diskWrite?: number;
		temperature?: number | null;
		/** The CPU's cores, when the server knows them: the CPU tile's context word. */
		cpuCores?: number;
		compact?: boolean;
		// One card of the stats, by its widget id, for the dashboard's grid of widgets (ADR-0395); the dashboard lays the cards out itself.
		only?: 'cpu' | 'memory' | 'network' | 'disk-io' | 'temperature';
	};

	let {
		cpuPercent = 0,
		memoryPercent = 0,
		memoryUsed = 0,
		memoryTotal = 0,
		networkUp = 0,
		networkDown = 0,
		diskRead = 0,
		diskWrite = 0,
		temperature = null,
		cpuCores = 0,
		compact = false,
		only = 'cpu'
	}: Props = $props();

	// The functions that read $t do it where the markup calls them: the texts follow the language.
	function formatRate(bytes: number): string {
		return $t('dashboard.rate', { size: formatBytes(bytes, $t) });
	}

	function getPercentColor(percent: number): string {
		if (percent < 70) return 'text-success';
		if (percent < 90) return 'text-warning';
		return 'text-error';
	}

	function getTempColor(temp: number): string {
		if (temp < 50) return 'text-success';
		if (temp < 70) return 'text-warning';
		return 'text-error';
	}
</script>

{#if compact}
	<div class="flex items-center gap-4 text-sm">
		<div class="flex items-center gap-1">
			<Cpu class="w-4 h-4 icon-cpu" />
			<span class={getPercentColor(cpuPercent)}>{cpuPercent.toFixed(0)}%</span>
		</div>
		<div class="flex items-center gap-1">
			<MemoryStick class="w-4 h-4 icon-memory" />
			<span class={getPercentColor(memoryPercent)}>{memoryPercent.toFixed(0)}%</span>
		</div>
		{#if temperature !== null}
			<div class="flex items-center gap-1">
				<Thermometer class="w-4 h-4 icon-temperature" />
				<span class={getTempColor(temperature)}>{$t('dashboard.celsius', { value: temperature })}</span>
			</div>
		{/if}
	</div>
{:else}
	<!-- A stat tile (ADR-0428): an icon chip in the widget's accent, the label and a context word;
	     the value in the text's colour in the normal range (the class text-success stays) and in
	     the status colour above the thresholds; a thin meter, or the rates with a plain arrow. -->
	<!-- CPU, memory, network and disk I/O summarize System > Resource Monitor, where an
	     administrator follows them: the label links there (ADR-0430). The temperature is shown here
	     alone. -->
	{#snippet header(Icon: typeof Cpu, chip: string, label: string, context: string, home = '/system#monitor')}
		<div class="stat-card-header">
			<span class="stat-chip {chip}" aria-hidden="true"><Icon class="w-4 h-4" /></span>
			{#if $isAdmin && home}
				<a href={home} class="stat-card-label hover:underline">{label}</a>
			{:else}
				<span class="stat-card-label">{label}</span>
			{/if}
			{#if context}<span class="stat-card-context">{context}</span>{/if}
		</div>
	{/snippet}
	<div class="stat-cell">
		<!-- CPU -->
		{#if only === 'cpu'}
		<Card padding="sm" class="stat-card">
			{@render header(Cpu, 'chip-cpu', $t('dashboard.stats.cpu'), cpuCores > 0 ? $t('dashboard.stats.cores', { n: cpuCores }) : '')}
			<p class="stat-card-value {getPercentColor(cpuPercent)}">{cpuPercent.toFixed(1)}%</p>
			<ProgressBar value={cpuPercent} size="sm" class="mt-auto pt-1" label={$t('dashboard.stats.cpu')} />
		</Card>
		{/if}

		<!-- Memory -->
		{#if only === 'memory'}
		<Card padding="sm" class="stat-card">
			{@render header(MemoryStick, 'chip-memory', $t('dashboard.stats.memory'), '')}
			<p class="stat-card-value {getPercentColor(memoryPercent)}">{memoryPercent.toFixed(1)}%</p>
			<ProgressBar value={memoryPercent} size="sm" class="mt-auto pt-1" label={$t('dashboard.stats.memory')} />
			<p class="stat-card-sublabel">{formatBytes(memoryUsed, $t)} / {formatBytes(memoryTotal, $t)}</p>
		</Card>
		{/if}

		<!-- Network: received, then sent -->
		{#if only === 'network'}
		<Card padding="sm" class="stat-card">
			{@render header(Network, 'chip-network', $t('dashboard.stats.network'), '')}
			<p class="flex items-baseline gap-2">
				<span class="w-4 text-xs text-content-muted" aria-hidden="true">↓</span>
				<span class="text-lg font-semibold tabular">{formatRate(networkDown)}</span>
			</p>
			<p class="flex items-baseline gap-2">
				<span class="w-4 text-xs text-content-muted" aria-hidden="true">↑</span>
				<span class="text-lg font-semibold tabular">{formatRate(networkUp)}</span>
			</p>
		</Card>
		{/if}

		<!-- Disk I/O: read, then written. A label goes above its rate where the card has no room for
		     both (ADR-0302). -->
		{#if only === 'disk-io'}
		<Card padding="sm" class="stat-card">
			{@render header(HardDrive, 'chip-storage', $t('dashboard.stats.diskIo'), '')}
			<p class="flex flex-wrap items-baseline gap-x-2">
				<span class="stat-card-sublabel">{$t('dashboard.stats.read')}</span>
				<span class="text-lg font-semibold tabular">{formatRate(diskRead)}</span>
			</p>
			<p class="flex flex-wrap items-baseline gap-x-2">
				<span class="stat-card-sublabel">{$t('dashboard.stats.write')}</span>
				<span class="text-lg font-semibold tabular">{formatRate(diskWrite)}</span>
			</p>
		</Card>
		{/if}

		<!-- Temperature, its meter against 90 °C -->
		{#if only === 'temperature'}
		<Card padding="sm" class="stat-card">
			{@render header(Thermometer, 'chip-temperature', $t('dashboard.stats.temperature'), '', '')}
			{#if temperature !== null}
				<p class="stat-card-value {getTempColor(temperature)}">{$t('dashboard.celsius', { value: temperature })}</p>
				<ProgressBar
					value={temperature}
					max={90}
					size="sm"
					variant={temperature < 50 ? 'success' : temperature < 70 ? 'warning' : 'error'}
					class="mt-auto pt-1"
					label={$t('dashboard.stats.temperature')}
					valueText={$t('dashboard.celsius', { value: temperature })}
				/>
			{:else}
				<p class="stat-card-value text-muted">{$t('dashboard.stats.none')}</p>
			{/if}
		</Card>
		{/if}
	</div>
{/if}

<style>
	/* The card fills its cell of the dashboard's grid, as tall as the tallest of its row. */
	.stat-cell,
	.stat-cell > :global(.card) {
		height: 100%;
	}
</style>
