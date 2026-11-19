<script lang="ts">
	// A mounted pool's balance (ADR-0379): its progress while it runs or is paused, then how it
	// ended; or why its status could not be read.
	import { AlertTriangle, CheckCircle, Loader2, Pause } from 'lucide-svelte';
	import { Alert, ProgressBar } from '$lib/components/ui';
	import type { BalanceStatus } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { balanceProgress } from './pools';

	type Props = {
		mounted: boolean;
		status: BalanceStatus | undefined;
		/** Why the status could not be read: shown instead of the last one. */
		error: string | undefined;
		percentFormat: Intl.NumberFormat;
	};

	let { mounted, status, error, percentFormat }: Props = $props();

	// What a balance that runs or is paused does, by its status.
	const BALANCE_STATES: Record<string, string> = {
		running: 'storage.balance.running',
		pausing: 'storage.balance.pausing',
		cancelling: 'storage.balance.cancelling',
		paused: 'storage.balance.paused'
	};

	/** The estimate of the time a balance needs still, in hours and minutes. */
	const timeLeft = (seconds: number): string => {
		const minutes = Math.ceil(seconds / 60);
		if (seconds < 60) return $t('storage.balance.leftSeconds');
		if (minutes < 60) return $t('storage.balance.leftMinutes', { minutes });
		return $t('storage.balance.leftHours', { hours: Math.floor(minutes / 60), minutes: minutes % 60 });
	};
</script>

{#if mounted && error}
	<Alert variant="warning" title={$t('storage.balance.statusError')} class="mt-3">
		{error}
	</Alert>
{:else if mounted && status && status.status !== 'idle'}
	<div class="mt-3 pt-3 border-t border-border space-y-2">
		<div class="flex flex-wrap items-center justify-between gap-2 text-sm">
			<span
				class="flex items-center gap-2 {status.status === 'paused'
					? 'text-warning'
					: 'text-info'}"
			>
				{#if status.status === 'paused'}
					<Pause class="w-4 h-4" />
				{:else}
					<Loader2 class="w-4 h-4 loading-spinner" />
				{/if}
				{$t(BALANCE_STATES[status.status] ?? 'storage.balance.running')}
			</span>
			{#if status.status === 'running' && status.seconds_left !== null}
				<span class="text-secondary">{timeLeft(status.seconds_left)}</span>
			{/if}
		</div>
		{#if status.expected && status.completed !== null}
			<ProgressBar
				value={balanceProgress(status)}
				variant="info"
				label={$t('storage.balance.progress')}
			/>
			<div class="flex flex-wrap justify-between gap-x-2 text-xs text-secondary">
				<span
					>{$t('storage.balance.chunks', {
						done: status.completed,
						n: status.expected
					})}</span
				>
				<span>{percentFormat.format(balanceProgress(status) / 100)}</span>
			</div>
		{/if}
		{#if status.status === 'paused'}
			<p class="text-xs text-secondary">{$t('storage.balance.pausedNote')}</p>
		{/if}
	</div>
{:else if mounted && status?.result === 'finished'}
	<div class="mt-3 pt-3 border-t border-border flex items-center gap-2 text-sm text-success">
		<CheckCircle class="w-4 h-4" />
		{$t('storage.balance.finished', {
			done: status.completed ?? 0,
			n: status.considered ?? 0
		})}
	</div>
{:else if mounted && status?.result === 'cancelled'}
	<div class="mt-3 pt-3 border-t border-border flex items-center gap-2 text-sm text-warning">
		<AlertTriangle class="w-4 h-4" />
		{$t('storage.balance.cancelled')}
	</div>
{:else if mounted && status?.result === 'failed'}
	<Alert variant="error" title={$t('storage.balance.failed')} class="mt-3">
		<span class="break-words">{status.error}</span>
	</Alert>
{/if}
