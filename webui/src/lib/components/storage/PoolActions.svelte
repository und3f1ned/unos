<script lang="ts">
	// The actions of a pool's card (ADR-0432): its two everyday ones, Volumes and Scrub (Mount for a
	// pool that is not mounted), as buttons, and the rest behind More: a balance (ADR-0379), a
	// disk's add and removal (ADR-0411), the unmount and the removal of the pool. An action that
	// cannot run now is disabled and says why. The controls of a job that runs (cancel a scrub,
	// pause, resume or cancel a balance) are the job's own, beside its progress.
	import { ChevronDown, Layers, Loader2, MinusCircle, Plug, Plus, RefreshCw, Scale, Trash2, Unplug } from 'lucide-svelte';
	import { Button, type IconComponent } from '$lib/components/ui';
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

	/** An action behind More: what the ActionMenu of the design system will list (ADR-0432). */
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

	// The reasons of the disabled actions behind More, each once.
	let reasons = $derived(
		[...new Set([pool.mounted && balanceShown ? balanceBlocked : null, pool.mounted ? disksBlocked : null])].filter(
			(reason): reason is string => reason !== null
		)
	);

	let open = $state(false);
	const panelId = $derived(`pool-more-${pool.name}`);
</script>

<div class="flex flex-wrap items-center gap-2">
	{#if pool.mounted}
		<Button variant="secondary" size="sm" icon={Layers} onclick={() => onvolumes(pool)}>
			{$t('storage.pool.volumes')}
		</Button>
		{#if scrub?.status !== 'running'}
			<!-- No scrub beside a balance or a disk removal that runs (ADR-0379, ADR-0411): the
			     tooltip and the text for screen readers say why. -->
			<span class="flex" title={scrubBlocked ? $t(scrubBlocked) : undefined}>
				<Button
					variant="secondary"
					size="sm"
					icon={startingScrub ? Loader2 : RefreshCw}
					disabled={startingScrub || scrubBlocked !== null}
					aria-describedby={scrubBlocked ? `${panelId}-scrub` : undefined}
					onclick={() => onscrub(pool)}
				>
					{$t('storage.pool.scrub')}
				</Button>
			</span>
			{#if scrubBlocked}
				<span id="{panelId}-scrub" class="sr-only">{$t(scrubBlocked)}</span>
			{/if}
		{/if}
	{:else}
		<Button variant="secondary" size="sm" icon={Plug} onclick={() => onmount(pool)}>
			{$t('storage.pool.mount')}
		</Button>
	{/if}
	<Button
		variant="ghost"
		size="sm"
		aria-expanded={open}
		aria-controls={panelId}
		aria-label={$t('storage.pool.moreOf', { name: pool.name })}
		onclick={() => (open = !open)}
	>
		{$t('storage.pool.more')}
		<ChevronDown class="w-4 h-4 transition-transform {open ? 'rotate-180' : ''}" aria-hidden="true" />
	</Button>
</div>

{#if open}
	<!-- The other actions, in a row under the header: the ActionMenu of the design system takes
	     this list when it comes (ADR-0432). -->
	<div id={panelId} class="basis-full flex flex-col gap-2 pt-3 border-t border-border">
		<div class="flex flex-wrap items-center gap-2" role="group" aria-label={$t('storage.pool.moreOf', { name: pool.name })}>
			{#each moreActions() as action (action.id)}
				<Button
					variant={action.danger ? 'ghost-danger' : 'secondary'}
					size="sm"
					icon={action.icon}
					disabled={!!action.blocked}
					title={action.blocked ? $t(action.blocked) : undefined}
					aria-label={action.danger ? $t('storage.pool.remove', { name: pool.name }) : undefined}
					onclick={action.run}
				>
					{action.label}
				</Button>
			{/each}
		</div>
		{#each reasons as reason (reason)}
			<p class="text-xs text-content-secondary">{$t(reason)}</p>
		{/each}
	</div>
{/if}
