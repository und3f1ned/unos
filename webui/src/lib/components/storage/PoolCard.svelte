<script lang="ts">
	// A pool of the Pools card: its name, health and mount at boot, its buttons, its profile, use,
	// disks and mount point, what is wrong with it, and the jobs on it.
	import { AlertCircle, AlertTriangle } from 'lucide-svelte';
	import { Alert, Badge, ProgressBar } from '$lib/components/ui';
	import type { BalanceStatus, DiskRemovalStatus, Pool, ScrubStatus } from '$lib/api/storage';
	import { formatPercent } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import BalanceProgress from './BalanceProgress.svelte';
	import PoolActions from './PoolActions.svelte';
	import RemovalProgress from './RemovalProgress.svelte';
	import ScrubProgress from './ScrubProgress.svelte';
	import { poolMountpoint, type BadgeVariant, type PoolHandlers } from './pools';

	type Props = PoolHandlers & {
		pool: Pool;
		scrub: ScrubStatus | undefined;
		/** Why the scrub status could not be read. */
		scrubError: string | undefined;
		startingScrub: boolean;
		balance: BalanceStatus | undefined;
		balanceError: string | undefined;
		balanceBusy: boolean;
		removal: DiskRemovalStatus | undefined;
		removalError: string | undefined;
		formatBytes: (bytes: number) => string;
		formatPercentage: (percent: number) => string;
		/** A RAID profile in the language of the page. */
		profileLabel: (profile: string) => string;
	};

	let {
		pool,
		scrub,
		scrubError,
		startingScrub,
		balance,
		balanceError,
		balanceBusy,
		removal,
		removalError,
		formatBytes,
		formatPercentage,
		profileLabel,
		...handlers
	}: Props = $props();

	// A pool's health as the server answers it, with the variant and the key of its badge.
	const HEALTH_BADGES: Record<string, { variant: BadgeVariant; key: string }> = {
		healthy: { variant: 'success', key: 'storage.health.healthy' },
		degraded: { variant: 'warning', key: 'storage.health.degraded' },
		unmounted: { variant: 'orange', key: 'storage.health.unmounted' },
		error: { variant: 'error', key: 'storage.health.error' },
		unknown: { variant: 'neutral', key: 'storage.health.unknown' }
	};

	/** The pool's health badge; a value without a key is shown as it comes. */
	let health = $derived.by((): { variant: BadgeVariant; label: string } => {
		const badge = HEALTH_BADGES[pool.health];
		return badge
			? { variant: badge.variant, label: $t(badge.key) }
			: { variant: 'error', label: pool.health || $t('storage.health.unknown') };
	});

	// The capacity's share in whole percent, in the language of the page: a job's progress beside it
	// shows tenths.

	// i18n-ignore: a command, the same in every language
	const RESET_COUNTERS = 'btrfs device stats -z';

	/** What is wrong with the pool, worst first, each a line of one alert. */
	type Issue = { tone: 'error' | 'warning'; title: string; text: string; command?: string };
	// Reads $t where the markup calls it: the texts follow the language.
	function issues(): Issue[] {
		const list: Issue[] = [];
		if (pool.held_by) {
			list.push({
				tone: 'warning',
				title: $t('storage.pool.heldTitle'),
				text:
					pool.held_by.length > 0
						? $t('storage.pool.held', { containers: pool.held_by.join(', ') })
						: $t('storage.pool.heldByHost')
			});
		}
		if (pool.missing_devices > 0) {
			list.push({
				tone: 'error',
				title: $t('storage.pool.missingTitle', { n: pool.missing_devices }),
				text: $t('storage.pool.missing')
			});
		}
		if (pool.forced_read_only) {
			list.push({
				tone: 'error',
				title: $t('storage.pool.forcedReadOnlyTitle'),
				text: $t('storage.pool.forcedReadOnly')
			});
		}
		if (pool.device_errors > 0) {
			list.push({
				tone: 'error',
				title: $t('storage.pool.deviceErrorsTitle', { n: pool.device_errors }),
				text: $t('storage.pool.deviceErrors'),
				command: `${RESET_COUNTERS} ${pool.mountpoint || poolMountpoint(pool.name)}`
			});
		}
		if (pool.raid_level === 'raid5' || pool.raid_level === 'raid6') {
			list.push({ tone: 'warning', title: $t('storage.pools.unsafeTitle'), text: $t('storage.pools.unsafe') });
		}
		if (pool.read_only && !pool.forced_read_only) {
			list.push({ tone: 'warning', title: $t('storage.pool.readOnlyTitle'), text: $t('storage.pool.readOnly') });
		}
		if (pool.health === 'unknown') {
			list.push({ tone: 'warning', title: $t('storage.pool.unreadableTitle'), text: $t('storage.pool.unreadable') });
		}
		return list;
	}
	const ISSUE_ICONS = { error: AlertCircle, warning: AlertTriangle };

	let poolIssues = $derived(issues());
	let used = $derived(pool.mounted && pool.total_size > 0 ? (pool.used_size / pool.total_size) * 100 : null);
</script>

<!-- A pool: its name and badges on the left and its actions on the right, on every
     pool; then a job that runs, with its controls; its facts as label over value (each a div of
     spans with a data-field); its use; what is wrong with it, in one alert; and how its last jobs
     ended. -->
<div class="card-nested space-y-4">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<div class="flex flex-wrap items-center gap-2 min-w-0">
			<h3 class="text-base font-semibold break-words">{pool.name}</h3>
			<Badge variant={health.variant} dot>{health.label}</Badge>
			{#if pool.mounted && !pool.persistent}
				<Badge variant="warning">{$t('storage.pool.notMountedAtBoot')}</Badge>
			{:else if pool.mounted}
				<Badge variant="neutral">{$t('storage.pool.mountedAtBoot')}</Badge>
			{:else if pool.persistent}
				<Badge variant="warning">{$t('storage.pool.inFstab')}</Badge>
			{/if}
		</div>
		<PoolActions {pool} {scrub} {balance} {removal} {startingScrub} {...handlers} />
	</div>

	<!-- The jobs that run, right under the header with their controls (ADR-0313, ADR-0379, ADR-0411). -->
	<ScrubProgress
		part="running"
		mounted={pool.mounted}
		status={scrub}
		error={scrubError}
		{formatBytes}
		{formatPercentage}
		oncancel={() => handlers.oncancelscrub(pool)}
	/>
	<BalanceProgress
		part="running"
		{pool}
		scrubRunning={scrub?.status === 'running'}
		status={balance}
		error={balanceError}
		{formatPercentage}
		busy={balanceBusy}
		onaction={(action) => handlers.onbalanceaction(pool, action)}
	/>
	<RemovalProgress
		part="running"
		mounted={pool.mounted}
		status={removal}
		error={removalError}
		{formatBytes}
		{formatPercentage}
	/>

	<div class="kv">
		<div data-field="raid">
			<span class="kv-label">{$t('storage.pool.raid')}</span>
			{#if pool.raid_level}
				<span class="kv-value flex flex-wrap items-center gap-2"
					>{profileLabel(pool.raid_level)}
					{#if pool.raid_level === 'raid5' || pool.raid_level === 'raid6'}
						<Badge variant="error">{$t('storage.pool.unsafe')}</Badge>
					{/if}</span
				>
			{:else}
				<!-- The profile is only read from a mounted filesystem. -->
				<span class="kv-value text-muted"
					>{$t(pool.mounted ? 'storage.pool.unknown' : 'storage.pool.unknownNotMounted')}</span
				>
			{/if}
		</div>
		<div data-field="devices">
			<span class="kv-label">{$t('storage.pool.devices')}</span>
			<span class="kv-value mono-xs break-words">{pool.devices.join(', ') || '-'}</span>
		</div>
		<div data-field="mount">
			<span class="kv-label">{$t('storage.pool.mountpoint')}</span>
			<span class="kv-value mono-xs break-words"
				>{pool.mounted ? pool.mountpoint || '-' : $t('storage.pool.notMounted')}</span
			>
		</div>
	</div>

	<!-- Its use: the sizes and the share over the bar; unknown while it is not mounted. -->
	<div data-field="used">
		<div class="flex flex-wrap items-baseline justify-between gap-x-3 mb-1.5 text-xs text-content-secondary">
			<span class="kv-label">{$t('storage.pool.used')}</span>
			{#if pool.mounted}
				<span class="kv-value tabular text-content"
					>{$t('storage.pool.usedOf', {
						used: formatBytes(pool.used_size),
						size: formatBytes(pool.total_size)
					})}{#if used !== null}<span class="text-content-secondary">&nbsp;· {formatPercent(used, $intlLocale)}</span
						>{/if}</span
				>
			{:else}
				<span class="kv-value text-muted">{$t('storage.pool.unknownNotMounted')}</span>
			{/if}
		</div>
		{#if used !== null}
			<ProgressBar value={used} variant="info" label={$t('storage.pool.capacityOf', { name: pool.name })} />
		{/if}
	</div>

	{#if poolIssues.length > 0}
		<!-- What is wrong, in one alert in the colour of the worst, a line each; with more than one,
		     a title counts them, so that the alert's icon is not doubled by the first line's. -->
		<Alert
			variant={poolIssues[0].tone}
			size="sm"
			title={poolIssues.length > 1 ? $t('storage.pool.issuesTitle', { n: poolIssues.length }) : ''}
		>
			<ul class="space-y-1.5">
				{#each poolIssues as issue (issue.title)}
					{@const Icon = ISSUE_ICONS[issue.tone]}
					<li class="flex items-start gap-2">
						{#if poolIssues.length > 1}
							<Icon
								class="w-3.5 h-3.5 mt-0.5 shrink-0 {issue.tone === 'error'
									? 'text-status-error'
									: 'text-status-warning'}"
								aria-hidden="true"
							/>
						{/if}
						<span class="min-w-0">
							<span class="font-medium">{issue.title}.</span>
							{issue.text}
							{#if issue.command}
								<span class="block mt-0.5 text-xs text-content-secondary"
									>{$t('storage.pool.resetWith')} <span class="mono-xs break-all">{issue.command}</span></span
								>
							{/if}
						</span>
					</li>
				{/each}
			</ul>
		</Alert>
	{/if}

	<!-- How the last jobs ended, or why their state could not be read. -->
	<ScrubProgress
		part="result"
		mounted={pool.mounted}
		status={scrub}
		error={scrubError}
		{formatBytes}
		{formatPercentage}
		oncancel={() => handlers.oncancelscrub(pool)}
	/>
	<BalanceProgress part="result" {pool} status={balance} error={balanceError} {formatPercentage} />
	<RemovalProgress
		part="result"
		mounted={pool.mounted}
		status={removal}
		error={removalError}
		{formatBytes}
		{formatPercentage}
	/>
</div>
