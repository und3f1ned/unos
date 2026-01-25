<script lang="ts">
	import { HardDrive, Database } from 'lucide-svelte';
	import { Card, Badge, ProgressBar } from '$lib/components/ui';

	export let pools: Array<{
		name: string;
		size: number;
		used: number;
		free: number;
		health: string;
	}> = [];
	export let compact: boolean = false;

	function formatBytes(bytes: number): string {
		if (bytes === 0) return '0 B';
		const k = 1024;
		const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
		const i = Math.floor(Math.log(bytes) / Math.log(k));
		return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
	}

	function getUsagePercent(used: number, size: number): number {
		return size > 0 ? (used / size) * 100 : 0;
	}

	function getHealthVariant(
		health: string
	): 'success' | 'warning' | 'error' | 'neutral' {
		switch (health?.toLowerCase()) {
			case 'healthy':
			case 'online':
				return 'success';
			case 'degraded':
				return 'warning';
			default:
				return 'error';
		}
	}

	$: totalSize = pools.reduce((acc, p) => acc + p.size, 0);
	$: totalUsed = pools.reduce((acc, p) => acc + p.used, 0);
	$: totalPercent = totalSize > 0 ? (totalUsed / totalSize) * 100 : 0;
</script>

{#if compact}
	<div class="flex items-center gap-2 text-sm">
		<Database class="w-4 h-4 icon-storage" />
		<span>{pools.length} pool{pools.length !== 1 ? 's' : ''}</span>
		<span class="text-muted">•</span>
		<span>{formatBytes(totalUsed)} / {formatBytes(totalSize)}</span>
	</div>
{:else}
	<Card>
		<div class="card-header">
			<h3 class="card-title">
				<HardDrive class="w-5 h-5 icon-storage" />
				Storage Pools
			</h3>
			<a href="/storage" class="text-sm text-info hover:text-blue-300 transition-colors"
				>Manage →</a
			>
		</div>

		{#if pools.length === 0}
			<p class="text-secondary text-sm">No storage pools configured</p>
		{:else}
			<div class="space-y-3">
				{#each pools as pool}
					{@const percent = getUsagePercent(pool.used, pool.size)}
					<div>
						<div class="flex items-center justify-between mb-1">
							<div class="flex items-center gap-2">
								<span class="font-medium">{pool.name}</span>
								<Badge variant={getHealthVariant(pool.health)}>{pool.health}</Badge>
							</div>
							<span class="text-sm text-secondary"
								>{formatBytes(pool.used)} / {formatBytes(pool.size)}</span
							>
						</div>
						<ProgressBar value={percent} variant="info" size="sm" />
					</div>
				{/each}
			</div>

			<!-- Total -->
			<div class="mt-4 pt-3 border-t border-border">
				<div class="flex items-center justify-between text-sm">
					<span class="text-secondary">Total Storage</span>
					<span
						>{formatBytes(totalUsed)} / {formatBytes(totalSize)} ({totalPercent.toFixed(
							1
						)}%)</span
					>
				</div>
			</div>
		{/if}
	</Card>
{/if}
