<script lang="ts">
	// A mounted pool's balance (ADR-0379): its progress while it runs or is paused, with its Pause,
	// Resume and Cancel (`part` running, under the pool's header), then how it ended, or why its
	// status could not be read (`part` result, at the foot of the pool).
	import { AlertTriangle, CheckCircle, Pause, Play } from 'lucide-svelte';
	import { Alert, Button, ProgressBar, StatusDot } from '$lib/components/ui';
	import type { BalanceStatus, Pool } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { balanceProgress, type BalanceAction } from './pools';

	type Props = {
		/** The balance that runs or is paused, or how the last one ended. */
		part: 'running' | 'result';
		pool: Pool;
		/** A scrub of the pool runs: a paused balance does not resume beside it (ADR-0379). */
		scrubRunning?: boolean;
		status: BalanceStatus | undefined;
		/** Why the status could not be read: shown instead of the last one. */
		error: string | undefined;
		percentFormat: Intl.NumberFormat;
		/** A pause, resume or cancel is on its way. */
		busy?: boolean;
		onaction?: (action: BalanceAction) => void;
	};

	let { part, pool, scrubRunning = false, status, error, percentFormat, busy = false, onaction }: Props = $props();
	const mounted = $derived(pool.mounted);

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

{#if part === 'result' && mounted && error}
	<Alert variant="warning" size="sm" title={$t('storage.balance.statusError')}>
		{error}
	</Alert>
{:else if part === 'running' && mounted && !error && status && status.status !== 'idle'}
	{@const paused = status.status === 'paused'}
	<div class="space-y-2">
		<div class="flex flex-wrap items-center justify-between gap-2 text-sm">
			<span class="flex items-center gap-2 {paused ? 'text-warning' : 'text-info'}">
				{#if paused}
					<Pause class="w-4 h-4" aria-hidden="true" />
				{:else}
					<StatusDot status="info" pulse />
				{/if}
				{$t(BALANCE_STATES[status.status] ?? 'storage.balance.running')}
			</span>
			<span class="flex flex-wrap items-center gap-2">
				{#if status.status === 'running' || paused}
					<!-- A resume is refused on a read-only pool and beside a scrub that runs (ADR-0379). -->
					<Button
						variant="secondary"
						size="sm"
						icon={paused ? Play : Pause}
						disabled={busy || (paused && (pool.read_only || scrubRunning))}
						onclick={() => onaction?.(paused ? 'resume' : 'pause')}
					>
						{$t(paused ? 'storage.pool.resumeBalance' : 'storage.pool.pauseBalance')}
					</Button>
				{/if}
				<!-- A cancel waits for a pause that waits for its chunk, which the kernel would make all
				     the same, and the kernel keeps a paused balance of a read-only pool for its next
				     read-write mount (ADR-0379). -->
				<Button
					variant="secondary"
					size="sm"
					disabled={busy || status.status === 'cancelling' || status.status === 'pausing' || (paused && pool.read_only)}
					onclick={() => onaction?.('cancel')}
				>
					{$t('storage.pool.cancelBalance')}
				</Button>
			</span>
		</div>
		{#if status.expected && status.completed !== null}
			<ProgressBar value={balanceProgress(status)} variant="info" label={$t('storage.balance.progress')} />
		{/if}
		<!-- How far, and how long it needs still: no chunk counts. -->
		<div class="flex flex-wrap justify-between gap-x-3 text-xs text-secondary tabular">
			{#if status.expected && status.completed !== null}
				<span>{$t('storage.balance.done', { percent: percentFormat.format(balanceProgress(status) / 100) })}</span>
			{/if}
			{#if status.status === 'running' && status.seconds_left !== null}
				<span>{timeLeft(status.seconds_left)}</span>
			{/if}
		</div>
		{#if paused}
			<p class="text-xs text-secondary">{$t('storage.balance.pausedNote')}</p>
		{/if}
	</div>
{:else if part === 'result' && mounted && !error && status?.status === 'idle' && status.result === 'finished'}
	<div class="pt-3 border-t border-border flex items-center gap-2 text-sm text-success">
		<CheckCircle class="w-4 h-4" aria-hidden="true" />
		{$t('storage.balance.finished')}
	</div>
{:else if part === 'result' && mounted && !error && status?.status === 'idle' && status.result === 'cancelled'}
	<div class="pt-3 border-t border-border flex items-center gap-2 text-sm text-warning">
		<AlertTriangle class="w-4 h-4" aria-hidden="true" />
		{$t('storage.balance.cancelled')}
	</div>
{:else if part === 'result' && mounted && !error && status?.status === 'idle' && status.result === 'failed'}
	<Alert variant="error" size="sm" title={$t('storage.balance.failed')}>
		<span class="break-words">{status.error}</span>
	</Alert>
{/if}
