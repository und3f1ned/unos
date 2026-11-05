<script lang="ts">
	import { HardDrive, Database } from 'lucide-svelte';
	import { Card, Badge, ProgressBar } from '$lib/components/ui';

	export let pools: Array<{
		name: string;
		size: number;
		used: number;
		free: number;
		health: string;
		mounted?: boolean;
		missingDevices?: number;
		deviceErrors?: number;
		readOnly?: boolean;
		/** btrfs made the filesystem read-only after a serious error. */
		forcedReadOnly?: boolean;
	}> = [];
	export let compact: boolean = false;

	type BadgeVariant = 'success' | 'warning' | 'error' | 'orange' | 'neutral';

	const healthBadges: Record<string, { variant: BadgeVariant; label: string }> = {
		healthy: { variant: 'success', label: 'Healthy' },
		degraded: { variant: 'warning', label: 'Degraded' },
		unmounted: { variant: 'orange', label: 'Not mounted' },
		error: { variant: 'error', label: 'Error' },
		unknown: { variant: 'neutral', label: 'Unknown' }
	};

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

	function getHealthBadge(health: string): { variant: BadgeVariant; label: string } {
		return healthBadges[health?.toLowerCase()] ?? { variant: 'error', label: health || 'Unknown' };
	}

	function isMounted(pool: (typeof pools)[number]): boolean {
		return pool.mounted ?? pool.health !== 'unmounted';
	}

	function problems(pool: (typeof pools)[number]): string[] {
		const list: string[] = [];
		if (pool.missingDevices) {
			list.push(`${pool.missingDevices} device${pool.missingDevices === 1 ? '' : 's'} missing`);
		}
		if (pool.deviceErrors) {
			list.push(`${pool.deviceErrors} device error${pool.deviceErrors === 1 ? '' : 's'}`);
		}
		if (pool.forcedReadOnly) list.push('forced read-only');
		else if (pool.readOnly) list.push('read-only');
		return list;
	}

	// Only a healthy pool mounted read-only on purpose is a warning; the rest are errors.
	function isWarning(pool: (typeof pools)[number]): boolean {
		return pool.health?.toLowerCase() === 'healthy' && !pool.forcedReadOnly;
	}

	// Usage is only known for mounted pools.
	$: mountedPools = pools.filter(isMounted);
	$: totalSize = mountedPools.reduce((acc, p) => acc + p.size, 0);
	$: totalUsed = mountedPools.reduce((acc, p) => acc + p.used, 0);
	$: totalPercent = totalSize > 0 ? (totalUsed / totalSize) * 100 : 0;
	$: problemCount = pools.filter((p) => p.health?.toLowerCase() !== 'healthy').length;
</script>

{#if compact}
	<div class="flex items-center gap-2 text-sm">
		<Database class="w-4 h-4 icon-storage" />
		<span>{pools.length} pool{pools.length !== 1 ? 's' : ''}</span>
		<span class="text-muted">•</span>
		<span>{formatBytes(totalUsed)} / {formatBytes(totalSize)}</span>
		{#if problemCount > 0}
			<span class="text-muted">•</span>
			<span class="text-warning">{problemCount} need{problemCount === 1 ? 's' : ''} attention</span>
		{/if}
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
					{@const badge = getHealthBadge(pool.health)}
					{@const mounted = isMounted(pool)}
					{@const issues = problems(pool)}
					<div>
						<div class="flex items-center justify-between mb-1">
							<div class="flex items-center gap-2">
								<span class="font-medium">{pool.name}</span>
								<Badge variant={badge.variant}>{badge.label}</Badge>
							</div>
							{#if mounted}
								<span class="text-sm text-secondary"
									>{formatBytes(pool.used)} / {formatBytes(pool.size)}</span
								>
							{:else}
								<span class="text-sm text-muted">Data not accessible</span>
							{/if}
						</div>
						{#if mounted}
							<ProgressBar value={getUsagePercent(pool.used, pool.size)} variant="info" size="sm" />
						{/if}
						{#if issues.length > 0}
							<p class="text-xs mt-1 {isWarning(pool) ? 'text-warning' : 'text-error'}">
								{issues.join(' • ')}
							</p>
						{/if}
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
