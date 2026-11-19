<script lang="ts">
	import { Cpu, HardDrive, MemoryStick, Network, Thermometer } from 'lucide-svelte';
	import { Card, ProgressBar } from '$lib/components/ui';
	import { t } from '$lib/i18n';
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
	<div class="stat-cell">
		<!-- CPU -->
		{#if only === 'cpu'}
		<Card>
			<div class="stat-card-header">
				<Cpu class="w-5 h-5 icon-cpu" />
				<span class="stat-card-label">{$t('dashboard.stats.cpu')}</span>
			</div>
			<p class="stat-card-value {getPercentColor(cpuPercent)}">{cpuPercent.toFixed(1)}%</p>
			<ProgressBar value={cpuPercent} size="sm" class="mt-2" />
		</Card>
		{/if}

		<!-- Memory -->
		{#if only === 'memory'}
		<Card>
			<div class="stat-card-header">
				<MemoryStick class="w-5 h-5 icon-memory" />
				<span class="stat-card-label">{$t('dashboard.stats.memory')}</span>
			</div>
			<p class="stat-card-value {getPercentColor(memoryPercent)}">{memoryPercent.toFixed(1)}%</p>
			<p class="stat-card-sublabel">{formatBytes(memoryUsed, $t)} / {formatBytes(memoryTotal, $t)}</p>
			<ProgressBar value={memoryPercent} size="sm" class="mt-2" />
		</Card>
		{/if}

		<!-- Network -->
		{#if only === 'network'}
		<Card>
			<div class="stat-card-header">
				<Network class="w-5 h-5 icon-network" />
				<span class="stat-card-label">{$t('dashboard.stats.network')}</span>
			</div>
			<div class="flex items-center gap-2">
				<span class="text-success">↓</span>
				<span class="text-lg font-semibold">{formatRate(networkDown)}</span>
			</div>
			<div class="flex items-center gap-2">
				<span class="icon-temperature">↑</span>
				<span class="text-lg font-semibold">{formatRate(networkUp)}</span>
			</div>
		</Card>
		{/if}

		<!-- Disk I/O: read, then written. A label goes above its rate where the card has no room for
		     both (ADR-0302). -->
		{#if only === 'disk-io'}
		<Card>
			<div class="stat-card-header">
				<HardDrive class="w-5 h-5 icon-storage flex-shrink-0" />
				<span class="stat-card-label">{$t('dashboard.stats.diskIo')}</span>
			</div>
			<p class="flex flex-wrap items-baseline gap-x-2">
				<span class="stat-card-sublabel">{$t('dashboard.stats.read')}</span>
				<span class="text-lg font-semibold">{formatRate(diskRead)}</span>
			</p>
			<p class="flex flex-wrap items-baseline gap-x-2">
				<span class="stat-card-sublabel">{$t('dashboard.stats.write')}</span>
				<span class="text-lg font-semibold">{formatRate(diskWrite)}</span>
			</p>
		</Card>
		{/if}

		<!-- Temperature -->
		{#if only === 'temperature'}
		<Card>
			<div class="stat-card-header">
				<Thermometer class="w-5 h-5 icon-temperature" />
				<span class="stat-card-label">{$t('dashboard.stats.temperature')}</span>
			</div>
			{#if temperature !== null}
				<p class="stat-card-value {getTempColor(temperature)}">{$t('dashboard.celsius', { value: temperature })}</p>
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
