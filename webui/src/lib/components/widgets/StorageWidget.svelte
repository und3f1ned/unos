<script lang="ts">
	import { HardDrive, Database } from 'lucide-svelte';
	import { Card, Badge, ProgressBar } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { formatBytes } from './bytes';

	type Props = {
		pools?: Array<{
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
		}>;
		compact?: boolean;
	};

	let { pools = [], compact = false }: Props = $props();

	type BadgeVariant = 'success' | 'warning' | 'error' | 'orange' | 'neutral';

	// The badge of each health that GET /storage/pools reports: its variant and its label, a key of
	// the catalog.
	const healthBadges: Record<string, { variant: BadgeVariant; key: string }> = {
		healthy: { variant: 'success', key: 'dashboard.health.healthy' },
		degraded: { variant: 'warning', key: 'dashboard.health.degraded' },
		unmounted: { variant: 'orange', key: 'dashboard.health.unmounted' },
		error: { variant: 'error', key: 'dashboard.health.error' },
		unknown: { variant: 'neutral', key: 'common.unknown' }
	};

	function getUsagePercent(used: number, size: number): number {
		return size > 0 ? (used / size) * 100 : 0;
	}

	// The functions that read $t do it where the markup calls them: the texts follow the language.
	// A health without a badge of its own is an error, shown as the server reports it.
	function getHealthBadge(health: string): { variant: BadgeVariant; label: string } {
		const value = health?.toLowerCase();
		if (Object.prototype.hasOwnProperty.call(healthBadges, value)) {
			const { variant, key } = healthBadges[value];
			return { variant, label: $t(key) };
		}
		return { variant: 'error', label: health || $t('common.unknown') };
	}

	function isMounted(pool: (typeof pools)[number]): boolean {
		return pool.mounted ?? pool.health !== 'unmounted';
	}

	function problems(pool: (typeof pools)[number]): string[] {
		const list: string[] = [];
		if (pool.missingDevices) {
			list.push($t('dashboard.storage.missing', { n: pool.missingDevices }));
		}
		if (pool.deviceErrors) {
			list.push($t('dashboard.storage.errors', { n: pool.deviceErrors }));
		}
		if (pool.forcedReadOnly) list.push($t('dashboard.storage.forcedReadOnly'));
		else if (pool.readOnly) list.push($t('dashboard.storage.readOnly'));
		return list;
	}

	// Only a healthy pool mounted read-only on purpose is a warning; the rest are errors.
	function isWarning(pool: (typeof pools)[number]): boolean {
		return pool.health?.toLowerCase() === 'healthy' && !pool.forcedReadOnly;
	}

	// Usage is only known for mounted pools.
	const mountedPools = $derived(pools.filter(isMounted));
	const totalSize = $derived(mountedPools.reduce((acc, p) => acc + p.size, 0));
	const totalUsed = $derived(mountedPools.reduce((acc, p) => acc + p.used, 0));
	const totalPercent = $derived(totalSize > 0 ? (totalUsed / totalSize) * 100 : 0);
	const problemCount = $derived(pools.filter((p) => p.health?.toLowerCase() !== 'healthy').length);
</script>

{#if compact}
	<div class="flex items-center gap-2 text-sm">
		<Database class="w-4 h-4 icon-storage" />
		<span>{$t('dashboard.storage.pools', { n: pools.length })}</span>
		<span class="text-muted">•</span>
		<span>{formatBytes(totalUsed, $t)} / {formatBytes(totalSize, $t)}</span>
		{#if problemCount > 0}
			<span class="text-muted">•</span>
			<span class="text-warning">{$t('dashboard.storage.attention', { n: problemCount })}</span>
		{/if}
	</div>
{:else}
	<Card>
		<div class="card-header">
			<h3 class="card-title">
				<HardDrive class="w-5 h-5 icon-storage" />
				{$t('dashboard.storage.title')}
			</h3>
			<a href="/storage" class="text-sm text-info hover:text-status-info transition-colors"
				>{$t('dashboard.manage')}</a
			>
		</div>

		{#if pools.length === 0}
			<p class="text-secondary text-sm">{$t('dashboard.storage.none')}</p>
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
									>{formatBytes(pool.used, $t)} / {formatBytes(pool.size, $t)}</span
								>
							{:else}
								<span class="text-sm text-muted">{$t('dashboard.storage.inaccessible')}</span>
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
					<span class="text-secondary">{$t('dashboard.storage.total')}</span>
					<span
						>{formatBytes(totalUsed, $t)} / {formatBytes(totalSize, $t)} ({totalPercent.toFixed(
							1
						)}%)</span
					>
				</div>
			</div>
		{/if}
	</Card>
{/if}
