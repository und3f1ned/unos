<script lang="ts">
	// The Pools card of the Storage page: a warning for the profiles that btrfs does not keep safe,
	// and a card for each pool with the jobs on it.
	import { Database } from 'lucide-svelte';
	import { Alert, Card } from '$lib/components/ui';
	import type { Pool } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import type { PoolJobs } from './jobs.svelte';
	import PoolCard from './PoolCard.svelte';
	import type { PoolHandlers } from './pools';

	type Props = PoolHandlers & {
		pools: Pool[];
		/** Why the pool list could not be loaded. */
		error: string;
		/** The jobs on the pools. */
		jobs: PoolJobs;
		formatBytes: (bytes: number) => string;
		percentFormat: Intl.NumberFormat;
		profileLabel: (profile: string) => string;
	};

	let { pools, error, jobs, formatBytes, percentFormat, profileLabel, ...handlers }: Props = $props();
</script>

<Card>
	<h2 class="section-title mb-4">
		<Database class="w-5 h-5" />
		{$t('storage.pools.title')}
	</h2>

	{#if pools.some(p => p.raid_level === 'raid5' || p.raid_level === 'raid6')}
		<Alert variant="warning" title={$t('storage.pools.unsafeTitle')}>{$t('storage.pools.unsafe')}</Alert>
	{/if}

	{#if error}
		<Alert variant="error" title={$t('storage.pools.loadError')}>{error}</Alert>
	{:else if pools.length === 0}
		<p class="text-muted">{$t('storage.pools.none')}</p>
	{:else}
		<div class="space-y-4">
			{#each pools as pool}
				<PoolCard
					{pool}
					scrub={jobs.scrub.statuses[pool.name]}
					scrubError={jobs.scrub.errors[pool.name]}
					startingScrub={jobs.startingScrub[pool.name] ?? false}
					balance={jobs.balance.statuses[pool.name]}
					balanceError={jobs.balance.errors[pool.name]}
					balanceBusy={jobs.balanceBusy[pool.name] ?? false}
					removal={jobs.removal.statuses[pool.name]}
					removalError={jobs.removal.errors[pool.name]}
					{formatBytes}
					{percentFormat}
					{profileLabel}
					{...handlers}
				/>
			{/each}
		</div>
	{/if}
</Card>
