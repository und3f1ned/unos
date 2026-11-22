<script lang="ts">
	// A mounted pool's scrub (ADR-0313): its progress while it runs, with its Cancel (`part`
	// running, under the pool's header), then the result of the last one, or why its status could not
	// be read (`part` result, at the foot of the pool).
	import { AlertTriangle, CheckCircle, XCircle } from 'lucide-svelte';
	import { Alert, Button, ProgressBar, StatusDot } from '$lib/components/ui';
	import type { ScrubStatus } from '$lib/api/storage';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDuration, formatRate, ONE_DECIMAL } from '$lib/format';
	import { formatDateTime } from '$lib/time';
	import { getScrubProgress } from './pools';

	type Props = {
		/** The scrub that runs, or how the last one ended. */
		part: 'running' | 'result';
		mounted: boolean;
		status: ScrubStatus | undefined;
		/** Why the status could not be read: shown instead of the last one. */
		error: string | undefined;
		formatBytes: (bytes: number) => string;
		formatPercentage: (percent: number) => string;
		/** Cancel was pressed: the scrub that runs is to stop. */
		oncancel?: () => void;
	};

	let { part, mounted, status, error, formatBytes, formatPercentage, oncancel }: Props = $props();

	// How a scrub that did not finish ended, as btrfs names it; another value is shown as it comes.
	const SCRUB_STOPPED: Record<string, string> = {
		aborted: 'storage.scrub.aborted',
		interrupted: 'storage.scrub.interrupted'
	};

	/** The result of a pool's last scrub, as btrfs reports it (ADR-0313). */
	const scrubResult = (status: ScrubStatus): { text: string; tone: 'success' | 'warning' | 'error' } => {
		if (status.status === 'finished') {
			return status.errors > 0
				? { text: $t('storage.scrub.errors', { n: status.errors }), tone: 'error' }
				: { text: $t('storage.scrub.noErrors'), tone: 'success' };
		}
		const stopped = SCRUB_STOPPED[status.status] ? $t(SCRUB_STOPPED[status.status]) : status.status;
		return {
			text:
				status.errors > 0
					? $t('storage.scrub.stoppedErrors', { stopped, n: status.errors })
					: $t('storage.scrub.stopped', { stopped }),
			tone: status.errors > 0 ? 'error' : 'warning'
		};
	};

	// When the last scrub started: the server answers it in UTC with its offset, the page shows it
	// in the browser's time zone and the date format of the page's language (ADR-0401).
	const scrubStarted = (value: string): string => formatDateTime(value, $intlLocale);

	const scrubResultStyles = {
		success: { icon: CheckCircle, class: 'text-success' },
		warning: { icon: AlertTriangle, class: 'text-warning' },
		error: { icon: XCircle, class: 'text-error' }
	};
</script>

{#if part === 'result' && mounted && error}
	<Alert variant="warning" size="sm" title={$t('storage.scrub.statusError')}>
		{error}
		{#if status?.status === 'running'}
			<!-- It was running when it was last read: it can still be cancelled. -->
			<div class="mt-2">
				<Button variant="secondary" size="sm" onclick={() => oncancel?.()}>
					{$t('storage.pool.cancelScrub')}
				</Button>
			</div>
		{/if}
	</Alert>
{:else if part === 'running' && mounted && !error && status?.status === 'running'}
	<div class="space-y-2">
		<div class="flex flex-wrap items-center justify-between gap-2 text-sm">
			<span class="flex items-center gap-2 text-info">
				<StatusDot status="info" pulse />
				{$t('storage.scrub.running')}
			</span>
			<span class="flex items-center gap-3">
				{#if status.duration}
					<span class="text-xs text-secondary tabular"
						>{formatDuration(status.duration, $intlLocale, $t, { smallest: 's' })}</span
					>
				{/if}
				<Button variant="secondary" size="sm" onclick={() => oncancel?.()}>
					{$t('storage.pool.cancelScrub')}
				</Button>
			</span>
		</div>
		{#if status.total_bytes && status.bytes_scrubbed}
			<ProgressBar value={getScrubProgress(status)} variant="info" label={$t('storage.scrub.progress')} />
			<div class="flex flex-wrap justify-between gap-x-3 text-xs text-secondary tabular">
				<span>{formatBytes(status.bytes_scrubbed)} / {formatBytes(status.total_bytes)}</span>
				<span>{formatPercentage(getScrubProgress(status))}</span>
				{#if status.rate}
					<span>{formatRate(status.rate, $intlLocale, $t, ONE_DECIMAL)}</span>
				{/if}
			</div>
		{/if}
	</div>
{:else if part === 'result' && mounted && !error && status && status.status !== 'idle' && status.status !== 'running'}
	{@const result = scrubResult(status)}
	{@const style = scrubResultStyles[result.tone]}
	{@const Icon = style.icon}
	<div class="pt-3 border-t border-border flex flex-wrap items-center justify-between gap-x-3 text-sm">
		<span class="flex items-center gap-2 {style.class}">
			<Icon class="w-4 h-4" aria-hidden="true" />
			{result.text}
		</span>
		<span class="text-xs text-muted">
			{#if status.last_run}
				{scrubStarted(status.last_run)}
			{/if}
		</span>
	</div>
{/if}
