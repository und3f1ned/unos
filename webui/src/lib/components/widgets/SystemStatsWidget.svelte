<script lang="ts">
	import { Cpu, MemoryStick, Network, Thermometer } from 'lucide-svelte';
	import { Card, ProgressBar } from '$lib/components/ui';

	export let cpuPercent: number = 0;
	export let memoryPercent: number = 0;
	export let memoryUsed: number = 0;
	export let memoryTotal: number = 0;
	export let networkUp: number = 0;
	export let networkDown: number = 0;
	export let temperature: number | null = null;
	export let compact: boolean = false;

	function formatBytes(bytes: number): string {
		if (bytes === 0) return '0 B';
		const k = 1024;
		const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
		const i = Math.floor(Math.log(bytes) / Math.log(k));
		return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
	}

	function formatRate(bytes: number): string {
		return formatBytes(bytes) + '/s';
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
				<span class={getTempColor(temperature)}>{temperature}°C</span>
			</div>
		{/if}
	</div>
{:else}
	<div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
		<!-- CPU -->
		<Card>
			<div class="stat-card-header">
				<Cpu class="w-5 h-5 icon-cpu" />
				<span class="stat-card-label">CPU</span>
			</div>
			<p class="stat-card-value {getPercentColor(cpuPercent)}">{cpuPercent.toFixed(1)}%</p>
			<ProgressBar value={cpuPercent} size="sm" class="mt-2" />
		</Card>

		<!-- Memory -->
		<Card>
			<div class="stat-card-header">
				<MemoryStick class="w-5 h-5 icon-memory" />
				<span class="stat-card-label">Memory</span>
			</div>
			<p class="stat-card-value {getPercentColor(memoryPercent)}">{memoryPercent.toFixed(1)}%</p>
			<p class="stat-card-sublabel">{formatBytes(memoryUsed)} / {formatBytes(memoryTotal)}</p>
			<ProgressBar value={memoryPercent} size="sm" class="mt-2" />
		</Card>

		<!-- Network -->
		<Card>
			<div class="stat-card-header">
				<Network class="w-5 h-5 icon-network" />
				<span class="stat-card-label">Network</span>
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

		<!-- Temperature -->
		<Card>
			<div class="stat-card-header">
				<Thermometer class="w-5 h-5 icon-temperature" />
				<span class="stat-card-label">Temperature</span>
			</div>
			{#if temperature !== null}
				<p class="stat-card-value {getTempColor(temperature)}">{temperature}°C</p>
			{:else}
				<p class="stat-card-value text-muted">N/A</p>
			{/if}
		</Card>
	</div>
{/if}
