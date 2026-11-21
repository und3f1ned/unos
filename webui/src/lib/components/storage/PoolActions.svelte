<script lang="ts">
	// The actions of a pool's card: its two everyday ones, Volumes and Scrub (Mount for a
	// pool that is not mounted), as buttons, and the rest in its ⋯ menu: a balance (ADR-0379), a
	// disk's add and removal (ADR-0411), the unmount and the removal of the pool. An action that
	// cannot run now is disabled and says why. The controls of a job that runs (cancel a scrub,
	// pause, resume or cancel a balance) are the job's own, beside its progress.
	import { Layers, MinusCircle, Plug, Plus, RefreshCw, Scale, Trash2, Unplug } from 'lucide-svelte';
	import { ActionMenu, Button, type IconComponent } from '$lib/components/ui';
	import type { BalanceStatus, DiskRemovalStatus, Pool, ScrubStatus } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { blockedReason, type PoolHandlers } from './pools';

	type Props = PoolHandlers & {
		pool: Pool;
		scrub: ScrubStatus | undefined;
		balance: BalanceStatus | undefined;
		removal: DiskRemovalStatus | undefined;
		/** The pool's scrub is starting. */
		startingScrub: boolean;
	};

	let {
		pool,
		scrub,
		balance,
		removal,
		startingScrub,
		onscrub,
		onbalance,
		onadddisk,
		onremovedisk,
		onvolumes,
		onmount,
		onunmount,
		ondelete
	}: Props = $props();

	let scrubBlocked = $derived(blockedReason('scrub', pool, scrub, balance, removal));
	let balanceBlocked = $derived(blockedReason('balance', pool, scrub, balance, removal));
	let disksBlocked = $derived(blockedReason('disks', pool, scrub, balance, removal));
	// A balance that runs or is paused has its controls beside its progress.
	let balanceShown = $derived(!balance || balance.status === 'idle');

	/** An action of the pool's menu. */
	type MoreAction = {
		id: string;
		label: string;
		icon: IconComponent;
		run: () => void;
		/** Why it cannot run now: a key of the catalog. */
		blocked?: string | null;
		danger?: boolean;
	};

	// Reads $t where the markup calls it: the labels follow the language.
	function moreActions(): MoreAction[] {
		const list: MoreAction[] = [];
		if (pool.mounted) {
			if (balanceShown) {
				list.push({ id: 'balance', label: $t('storage.pool.balance'), icon: Scale, run: () => onbalance(pool), blocked: balanceBlocked });
			}
			list.push(
				{ id: 'add-disk', label: $t('storage.pool.addDisk'), icon: Plus, run: () => onadddisk(pool), blocked: disksBlocked },
				{ id: 'remove-disk', label: $t('storage.pool.removeDisk'), icon: MinusCircle, run: () => onremovedisk(pool), blocked: disksBlocked },
				{ id: 'unmount', label: $t('storage.pool.unmount'), icon: Unplug, run: () => onunmount(pool) }
			);
		}
		list.push({ id: 'remove', label: $t('storage.pool.removeShort'), icon: Trash2, run: () => ondelete(pool), danger: true });
		return list;
	}

	// The menu's items; one that cannot run now is disabled and says why under its label.
	const items = $derived(
		moreActions().map((action) => ({
			label: action.label,
			icon: action.icon,
			onclick: action.run,
			danger: action.danger,
			disabled: !!action.blocked,
			disabledReason: action.blocked ? $t(action.blocked) : undefined
		}))
	);
</script>

<div class="flex flex-wrap items-center gap-2">
	{#if pool.mounted}
		<Button variant="secondary" size="sm" icon={Layers} onclick={() => onvolumes(pool)}>
			{$t('storage.pool.volumes')}
		</Button>
		{#if scrub?.status !== 'running'}
			<!-- No scrub beside a balance or a disk removal that runs (ADR-0379, ADR-0411): the
			     button says why. -->
			<Button
				variant="secondary"
				size="sm"
				icon={RefreshCw}
				loading={startingScrub}
				disabled={scrubBlocked !== null}
				disabledReason={scrubBlocked ? $t(scrubBlocked) : ''}
				onclick={() => onscrub(pool)}
			>
				{$t('storage.pool.scrub')}
			</Button>
		{/if}
	{:else}
		<Button variant="secondary" size="sm" icon={Plug} onclick={() => onmount(pool)}>
			{$t('storage.pool.mount')}
		</Button>
	{/if}
	<!-- The other actions, in the pool's ⋯ menu (ADR-0432), the removal last. -->
	<ActionMenu name={pool.name} {items} />
</div>
