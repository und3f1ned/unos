<script lang="ts">
	// The Pools card of the Storage page, first on the page: a card for each pool with the jobs on
	// it, or, without a pool, how many disks are free for one: the header's Create pool makes it,
	// and the empty state repeats no button (ADR-0431, ADR-0432).
	import { Database } from 'lucide-svelte';
	import { Alert, Card, EmptyState } from '$lib/components/ui';
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
		/** The disks that a new pool can take; null when the disks could not be read. */
		freeDisks: number | null;
	};

	let { pools, error, jobs, formatBytes, percentFormat, profileLabel, freeDisks, ...handlers }: Props = $props();
</script>

<Card class="space-y-4">
	<h2 class="section-title">
		<Database class="w-5 h-5 text-content-secondary" aria-hidden="true" />
		{$t('storage.pools.title')}
	</h2>

	{#if error}
		<!-- A plain title, the server's reason under it; the header's Refresh tries again. -->
		<Alert variant="error" size="sm" title={$t('storage.pools.loadError')}>
			<span class="text-xs text-content-secondary break-words">{error}</span>
		</Alert>
	{:else if pools.length === 0}
		<EmptyState
			compact
			icon={Database}
			title={$t('storage.pools.none')}
			description={freeDisks === null
				? $t('storage.pools.noneHint')
				: $t('storage.pools.freeDisks', { n: freeDisks })}
		/>
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
