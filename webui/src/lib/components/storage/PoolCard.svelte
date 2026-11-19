<script lang="ts">
	// A pool of the Pools card: its name, health and mount at boot, its buttons, its profile, use,
	// disks and mount point, what is wrong with it, and the jobs on it.
	import { Alert, Badge, ProgressBar } from '$lib/components/ui';
	import type { BalanceStatus, DiskRemovalStatus, Pool, ScrubStatus } from '$lib/api/storage';
	import { t } from '$lib/i18n';
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
		percentFormat: Intl.NumberFormat;
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
		percentFormat,
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

	// i18n-ignore: a command, the same in every language
	const RESET_COUNTERS = 'btrfs device stats -z';
</script>

<div class="card-nested">
	<div class="flex flex-wrap items-center justify-between mb-2 gap-2">
		<div class="flex flex-wrap items-center gap-2">
			<h3 class="font-semibold">{pool.name}</h3>
			<Badge variant={health.variant}>{health.label}</Badge>
			{#if pool.mounted && !pool.persistent}
				<Badge variant="warning">{$t('storage.pool.notMountedAtBoot')}</Badge>
			{:else if pool.mounted}
				<Badge variant="neutral">{$t('storage.pool.mountedAtBoot')}</Badge>
			{:else if pool.persistent}
				<Badge variant="warning">{$t('storage.pool.inFstab')}</Badge>
			{/if}
		</div>
		<PoolActions
			{pool}
			{scrub}
			{balance}
			{removal}
			{startingScrub}
			{balanceBusy}
			{...handlers}
		/>
	</div>

	<div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
		<div>
			<span class="text-secondary">{$t('storage.pool.raid')}</span>
			{#if pool.raid_level}
				<span class="ml-2">{profileLabel(pool.raid_level)}</span>
				{#if pool.raid_level === 'raid5' || pool.raid_level === 'raid6'}
					<Badge variant="error">{$t('storage.pool.unsafe')}</Badge>
				{/if}
			{:else}
				<!-- The profile is only read from a mounted filesystem. -->
				<span class="ml-2 text-muted"
					>{$t(pool.mounted ? 'storage.pool.unknown' : 'storage.pool.unknownNotMounted')}</span
				>
			{/if}
		</div>
		<div>
			<span class="text-secondary">{$t('storage.pool.used')}</span>
			{#if pool.mounted}
				<span class="ml-2"
					>{formatBytes(pool.used_size)} / {formatBytes(pool.total_size)}</span
				>
			{:else}
				<span class="ml-2 text-muted">{$t('storage.pool.unknownNotMounted')}</span>
			{/if}
		</div>
		<div>
			<span class="text-secondary">{$t('storage.pool.devices')}</span>
			<span class="ml-2 mono-xs break-words">{pool.devices.join(', ') || '-'}</span>
		</div>
		<div>
			<span class="text-secondary">{$t('storage.pool.mountpoint')}</span>
			<span class="ml-2 mono-xs break-words"
				>{pool.mounted ? pool.mountpoint || '-' : $t('storage.pool.notMounted')}</span
			>
		</div>
	</div>

	{#if pool.mounted && pool.total_size > 0}
		<ProgressBar
			value={(pool.used_size / pool.total_size) * 100}
			variant="info"
			class="mt-3"
		/>
	{/if}

	{#if pool.missing_devices > 0}
		<Alert variant="error" title={$t('storage.pool.missingTitle')} class="mt-3">
			{$t('storage.pool.missing', { n: pool.missing_devices })}
		</Alert>
	{/if}
	{#if pool.forced_read_only}
		<Alert variant="error" title={$t('storage.pool.forcedReadOnlyTitle')} class="mt-3">
			{$t('storage.pool.forcedReadOnly')}
		</Alert>
	{:else if pool.read_only}
		<Alert variant="warning" title={$t('storage.pool.readOnlyTitle')} class="mt-3">
			{$t('storage.pool.readOnly')}
		</Alert>
	{/if}
	{#if pool.device_errors > 0}
		<Alert variant="error" title={$t('storage.pool.deviceErrorsTitle')} class="mt-3">
			{$t('storage.pool.deviceErrors', { n: pool.device_errors })}
			<span class="mono-xs"
				>{RESET_COUNTERS} {pool.mountpoint || poolMountpoint(pool.name)}</span
			>.
		</Alert>
	{/if}
	{#if pool.health === 'unknown'}
		<Alert variant="warning" title={$t('storage.pool.unreadableTitle')} class="mt-3">
			{$t('storage.pool.unreadable')}
		</Alert>
	{/if}

	<!-- Scrub Status: progress while it runs, then the result (ADR-0313) -->
	<ScrubProgress mounted={pool.mounted} status={scrub} error={scrubError} {formatBytes} {percentFormat} />

	<!-- Balance: its progress while it runs or is paused, then how it ended (ADR-0379) -->
	<BalanceProgress mounted={pool.mounted} status={balance} error={balanceError} {percentFormat} />

	<!-- A disk removal: its progress while it runs, then how it ended (ADR-0411) -->
	<RemovalProgress mounted={pool.mounted} status={removal} error={removalError} {formatBytes} {percentFormat} />
</div>
