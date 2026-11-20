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
		/** The pools could not be read: no "No pools yet" (ADR-0432). */
		failed?: boolean;
	};

	let { pools = [], compact = false, failed = false }: Props = $props();

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
	<!-- The total on top, then a row per pool: its name and health, its use and a bar, and its
	     problems in the status colour (ADR-0428). The card is as tall as its neighbour: the room
	     falls below. -->
	<Card class="h-full flex flex-col">
		<div class="card-header">
			<h3 class="card-title">
				<HardDrive class="w-5 h-5 text-content-secondary" aria-hidden="true" />
				{$t('dashboard.storage.title')}
			</h3>
			<a href="/storage" class="link text-[13px]">{$t('dashboard.manage')}</a>
		</div>

		{#if failed}
			<!-- The banner above names the reason and links to Storage. -->
			<p class="text-sm text-error">{$t('dashboard.issue.poolsFailed')}</p>
		{:else if pools.length === 0}
			<!-- No pool yet: where one is made, which the header's Manage opens (ADR-0431, ADR-0432). -->
			<p class="text-sm text-secondary">{$t('dashboard.storage.none')}</p>
		{:else}
			<div class="pb-3.5 mb-3.5 border-b border-border">
				<p class="text-xs text-content-secondary">{$t('dashboard.storage.total')}</p>
				<p class="mt-0.5 text-lg font-semibold tabular">
					{formatBytes(totalUsed, $t)} / {formatBytes(totalSize, $t)} ({totalPercent.toFixed(1)}%)
				</p>
			</div>
			<!-- A row per pool, the same on every screen (ADR-0432): its name and health with its share
			     on the right, the bar, then its use and its problems below. An unmounted pool's badge
			     says so: no line repeats it. -->
			<div class="space-y-3">
				{#each pools as pool}
					{@const badge = getHealthBadge(pool.health)}
					{@const mounted = isMounted(pool)}
					{@const issues = problems(pool)}
					<div>
						<div class="flex items-center justify-between gap-3">
							<div class="flex items-center gap-2 min-w-0">
								<span class="font-medium truncate" title={pool.name}>{pool.name}</span>
								<Badge variant={badge.variant}>{badge.label}</Badge>
							</div>
							{#if mounted}
								<span class="text-[13px] text-secondary tabular whitespace-nowrap"
									>{getUsagePercent(pool.used, pool.size).toFixed(0)}%</span
								>
							{/if}
						</div>
						{#if mounted}
							<ProgressBar
								value={getUsagePercent(pool.used, pool.size)}
								variant="info"
								class="mt-2"
								label={pool.name}
							/>
						{/if}
						{#if mounted || issues.length > 0}
							<div class="mt-1.5 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-xs">
								{#if issues.length > 0}
									<span data-field="issues" class={isWarning(pool) ? 'text-warning' : 'text-error'}>{issues.join(' · ')}</span>
								{/if}
								{#if mounted}
									<span data-field="used" class="ml-auto text-secondary tabular whitespace-nowrap"
										>{$t('dashboard.storage.usedOf', {
											used: formatBytes(pool.used, $t),
											size: formatBytes(pool.size, $t)
										})}</span
									>
								{/if}
							</div>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</Card>
{/if}
