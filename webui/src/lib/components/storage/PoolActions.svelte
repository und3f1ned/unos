<script lang="ts">
	// The buttons of a pool's card: the jobs on a mounted pool (scrub ADR-0313, balance ADR-0379,
	// disk add and removal ADR-0411), its volumes, mount or unmount, and its removal.
	import {
		Layers,
		Loader2,
		MinusCircle,
		Pause,
		Play,
		Plug,
		Plus,
		RefreshCw,
		Scale,
		Trash2,
		Unplug,
		XCircle
	} from 'lucide-svelte';
	import { Button } from '$lib/components/ui';
	import type { BalanceStatus, DiskRemovalStatus, Pool, ScrubStatus } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { balanceRuns, diskChangeBlocked, removalRuns, type PoolHandlers } from './pools';

	type Props = PoolHandlers & {
		pool: Pool;
		scrub: ScrubStatus | undefined;
		balance: BalanceStatus | undefined;
		removal: DiskRemovalStatus | undefined;
		/** The pool's scrub is starting. */
		startingScrub: boolean;
		/** A pause, resume or cancel of the pool's balance is on its way. */
		balanceBusy: boolean;
	};

	let {
		pool,
		scrub,
		balance,
		removal,
		startingScrub,
		balanceBusy,
		onscrub,
		oncancelscrub,
		onbalance,
		onbalanceaction,
		onadddisk,
		onremovedisk,
		onvolumes,
		onmount,
		onunmount,
		ondelete
	}: Props = $props();

	let changeBlocked = $derived(diskChangeBlocked(pool, scrub, balance, removal));
</script>

<div class="flex flex-wrap items-center gap-2">
	{#if pool.mounted}
		{#if scrub?.status === 'running'}
			<Button
				variant="warning"
				size="sm"
				icon={XCircle}
				onclick={() => oncancelscrub(pool)}
			>
				{$t('storage.pool.cancelScrub')}
			</Button>
		{:else}
			<!-- No scrub beside a balance or a disk removal that runs (ADR-0379, ADR-0411). -->
			<Button
				variant="secondary"
				size="sm"
				icon={startingScrub ? Loader2 : RefreshCw}
				disabled={startingScrub ||
					balanceRuns(balance) ||
					removalRuns(removal)}
				onclick={() => onscrub(pool)}
			>
				{$t('storage.pool.scrub')}
			</Button>
		{/if}
		{#if balance?.status === 'running' || balance?.status === 'paused'}
			{@const paused = balance.status === 'paused'}
			<!-- A resume is refused on a read-only pool and beside a scrub that runs (ADR-0379). -->
			<Button
				variant="secondary"
				size="sm"
				icon={paused ? Play : Pause}
				disabled={balanceBusy ||
					(paused && (pool.read_only || scrub?.status === 'running'))}
				onclick={() => onbalanceaction(pool, paused ? 'resume' : 'pause')}
			>
				{$t(paused ? 'storage.pool.resumeBalance' : 'storage.pool.pauseBalance')}
			</Button>
		{/if}
		{#if balance && balance.status !== 'idle'}
			<!-- A cancel waits for a pause that waits for its chunk, which the kernel would
			     make all the same, and the kernel keeps a paused balance of a read-only
			     pool for its next read-write mount (ADR-0379). -->
			<Button
				variant="warning"
				size="sm"
				icon={XCircle}
				disabled={balanceBusy ||
					balance.status === 'cancelling' ||
					balance.status === 'pausing' ||
					(balance.status === 'paused' && pool.read_only)}
				onclick={() => onbalanceaction(pool, 'cancel')}
			>
				{$t('storage.pool.cancelBalance')}
			</Button>
		{:else}
			<!-- Not on a read-only pool, nor beside a scrub or a disk removal that runs
			     (ADR-0379, ADR-0411). -->
			<Button
				variant="secondary"
				size="sm"
				icon={Scale}
				disabled={pool.read_only ||
					scrub?.status === 'running' ||
					removalRuns(removal)}
				onclick={() => onbalance(pool)}
			>
				{$t('storage.pool.balance')}
			</Button>
		{/if}
		<!-- One job per pool, and only on a writable pool with all its disks (ADR-0411). -->
		<Button
			variant="secondary"
			size="sm"
			icon={Plus}
			disabled={changeBlocked}
			onclick={() => onadddisk(pool)}
		>
			{$t('storage.pool.addDisk')}
		</Button>
		<Button
			variant="secondary"
			size="sm"
			icon={MinusCircle}
			disabled={changeBlocked}
			onclick={() => onremovedisk(pool)}
		>
			{$t('storage.pool.removeDisk')}
		</Button>
		<Button
			variant="secondary"
			size="sm"
			icon={Layers}
			onclick={() => onvolumes(pool)}
		>
			{$t('storage.pool.volumes')}
		</Button>
		<Button
			variant="secondary"
			size="sm"
			icon={Unplug}
			onclick={() => onunmount(pool)}
		>
			{$t('storage.pool.unmount')}
		</Button>
	{:else}
		<Button
			variant="primary"
			size="sm"
			icon={Plug}
			onclick={() => onmount(pool)}
		>
			{$t('storage.pool.mount')}
		</Button>
	{/if}
	<Button
		variant="ghost"
		size="icon-sm"
		onclick={() => ondelete(pool)}
	>
		<Trash2 class="w-4 h-4 text-error" />
		<span class="sr-only">{$t('storage.pool.remove', { name: pool.name })}</span>
	</Button>
</div>
