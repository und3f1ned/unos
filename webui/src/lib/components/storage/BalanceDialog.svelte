<script lang="ts">
	// The Balance dialog (ADR-0379): a balance of the chunks up to a usage limit, or of every chunk,
	// which needs its confirmation.
	import { Alert, Button, Checkbox, Input, Modal, Select } from '$lib/components/ui';
	import { BALANCE_DEFAULT_USAGE, startBalance, type BalanceStatus, type Pool } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { messageOf } from './pools';

	type Props = {
		/** The pool to balance; the dialog is open while it is set. */
		pool: Pool | null;
		onclose: () => void;
		/** The balance started: its status, before the dialog closes. */
		onstarted: (pool: string, status: BalanceStatus) => void;
	};

	let { pool, onclose, onstarted }: Props = $props();

	// A usage limit or every chunk, and the confirmation of the latter.
	let balanceKind = $state('usage');
	let balanceUsage = $state(String(BALANCE_DEFAULT_USAGE));
	let balanceConfirmed = $state(false);
	let balanceStarting = $state(false);
	let balanceError = $state('');

	let balanceFull = $derived(balanceKind === 'full');
	let balanceLimit = $derived(/^\d{1,2}$/.test(balanceUsage.trim()) ? Number(balanceUsage.trim()) : 0);
	let canStartBalance = $derived(balanceFull ? balanceConfirmed : balanceLimit >= 1 && balanceLimit <= 99);

	// Every chunk needs its confirmation again after the user chose the usage limit.
	function chooseBalanceKind(kind: string) {
		if (kind !== 'full') balanceConfirmed = false;
	}

	// The dialog opens on the usage limit: its choices are reset when it closes, while it shows
	// nothing.
	function closeBalance() {
		if (balanceStarting) return;
		balanceKind = 'usage';
		balanceUsage = String(BALANCE_DEFAULT_USAGE);
		balanceConfirmed = false;
		balanceError = '';
		onclose();
	}

	async function handleStartBalance() {
		if (!pool || !canStartBalance) return;
		const poolName = pool.name;
		balanceStarting = true;
		balanceError = '';
		try {
			onstarted(poolName, await startBalance(poolName, balanceFull ? { full: true } : { usage: balanceLimit }));
			balanceStarting = false;
			closeBalance();
		} catch (e) {
			balanceError = messageOf(e);
		} finally {
			balanceStarting = false;
		}
	}
</script>

<Modal
	open={pool !== null}
	title={$t('storage.balance.title', { name: pool?.name ?? '' })}
	size="md"
	closable={!balanceStarting}
	onclose={closeBalance}
>
	{#if pool}
		<p class="text-sm text-secondary">{$t('storage.balance.intro')}</p>
		<Select
			label={$t('storage.balance.what')}
			bind:value={balanceKind}
			onchange={chooseBalanceKind}
			options={[
				{ value: 'usage', label: $t('storage.balance.usageOption') },
				{ value: 'full', label: $t('storage.balance.fullOption') }
			]}
		/>
		{#if balanceFull}
			<Alert variant="warning" title={$t('storage.balance.fullTitle')}>
				{$t('storage.balance.fullWarning')}
				<Checkbox
					bind:checked={balanceConfirmed}
					label={$t('storage.balance.fullConfirm', { name: pool.name })}
					class="mt-3"
				/>
			</Alert>
		{:else}
			<Input
				type="number"
				label={$t('storage.balance.usage')}
				bind:value={balanceUsage}
				hint={$t('storage.balance.usageHint')}
				error={canStartBalance ? '' : $t('storage.balance.usageInvalid')}
			/>
		{/if}
		{#if balanceError}
			<Alert variant="error" title={$t('storage.balance.startFailed')}>{balanceError}</Alert>
		{/if}
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={balanceStarting} onclick={closeBalance}>
			{$t('common.cancel')}
		</Button>
		<Button
			variant={balanceFull ? 'warning' : 'primary'}
			class="flex-1"
			loading={balanceStarting}
			disabled={!canStartBalance}
			onclick={handleStartBalance}
		>
			{$t('storage.balance.start')}
		</Button>
	{/snippet}
</Modal>
