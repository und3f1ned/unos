<script lang="ts">
	// A mounted pool's scrub (ADR-0313): its progress while it runs, then the result of the last
	// one; or why its status could not be read.
	import { AlertTriangle, CheckCircle, Loader2, XCircle } from 'lucide-svelte';
	import { Alert, ProgressBar } from '$lib/components/ui';
	import type { ScrubStatus } from '$lib/api/storage';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { formatDuration, getScrubProgress } from './pools';

	type Props = {
		mounted: boolean;
		status: ScrubStatus | undefined;
		/** Why the status could not be read: shown instead of the last one. */
		error: string | undefined;
		formatBytes: (bytes: number) => string;
		percentFormat: Intl.NumberFormat;
	};

	let { mounted, status, error, formatBytes, percentFormat }: Props = $props();

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

{#if mounted && error}
	<Alert variant="warning" title={$t('storage.scrub.statusError')} class="mt-3">
		{error}
	</Alert>
{:else if mounted && status && status.status !== 'idle'}
	<div class="mt-3 pt-3 border-t border-border">
		{#if status.status === 'running'}
			<div class="space-y-2">
				<div class="flex items-center justify-between text-sm">
					<span class="flex items-center gap-2 text-info">
						<Loader2 class="w-4 h-4 loading-spinner" />
						{$t('storage.scrub.running')}
					</span>
					<span class="text-secondary">
						{#if status.duration}
							{formatDuration(status.duration)}
						{/if}
					</span>
				</div>
				{#if status.total_bytes && status.bytes_scrubbed}
					<ProgressBar
						value={getScrubProgress(status)}
						variant="info"
						label={$t('storage.scrub.progress')}
					/>
					<div class="flex justify-between text-xs text-secondary">
						<span
							>{formatBytes(status.bytes_scrubbed)} / {formatBytes(
								status.total_bytes
							)}</span
						>
						<span>{percentFormat.format(getScrubProgress(status) / 100)}</span>
						{#if status.rate}
							<span>{$t('storage.scrub.rate', { rate: formatBytes(status.rate) })}</span>
						{/if}
					</div>
				{/if}
			</div>
		{:else}
			{@const result = scrubResult(status)}
			{@const style = scrubResultStyles[result.tone]}
			{@const Icon = style.icon}
			<div class="flex items-center justify-between text-sm">
				<span class="flex items-center gap-2 {style.class}">
					<Icon class="w-4 h-4" />
					{result.text}
				</span>
				<span class="text-xs text-muted">
					{#if status.last_run}
						{scrubStarted(status.last_run)}
					{/if}
				</span>
			</div>
		{/if}
	</div>
{/if}
