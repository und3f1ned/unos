<script lang="ts">
	// A disk removal from a mounted pool (ADR-0411): its progress while it runs (`part` running,
	// under the pool's header), then how it ended, or why its status could not be read (`part`
	// result, at the foot of the pool: ADR-0432).
	import { CheckCircle, Loader2 } from 'lucide-svelte';
	import { Alert, ProgressBar } from '$lib/components/ui';
	import type { DiskRemovalStatus } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { removalProgress } from './pools';

	type Props = {
		/** The removal that runs, or how the last one ended. */
		part: 'running' | 'result';
		mounted: boolean;
		status: DiskRemovalStatus | undefined;
		/** Why the status could not be read: shown instead of the last one. */
		error: string | undefined;
		formatBytes: (bytes: number) => string;
		percentFormat: Intl.NumberFormat;
	};

	let { part, mounted, status, error, formatBytes, percentFormat }: Props = $props();
</script>

{#if part === 'result' && mounted && error}
	<Alert variant="warning" size="sm" title={$t('storage.removal.statusError')}>
		{error}
	</Alert>
{:else if part === 'running' && mounted && !error && status?.status === 'running'}
	<div class="space-y-2">
		<span class="flex items-center gap-2 text-sm text-info break-all">
			<Loader2 class="w-4 h-4 loading-spinner shrink-0" />
			{$t('storage.removal.running', { device: status.device ?? '' })}
		</span>
		{#if status.total}
			<ProgressBar
				value={removalProgress(status)}
				variant="info"
				label={$t('storage.removal.progress')}
			/>
			<div class="flex flex-wrap justify-between gap-x-2 text-xs text-secondary">
				<span
					>{$t('storage.removal.left', {
						left: formatBytes(status.left ?? 0),
						total: formatBytes(status.total)
					})}</span
				>
				<span>{percentFormat.format(removalProgress(status) / 100)}</span>
			</div>
		{:else if status.left !== null}
			<p class="text-xs text-secondary">
				{$t('storage.removal.leftOnly', { left: formatBytes(status.left) })}
			</p>
		{/if}
	</div>
{:else if part === 'result' && mounted && !error && status?.status !== 'running' && status?.result === 'finished'}
	<div
		class="pt-3 border-t border-border flex items-center gap-2 text-sm text-success break-all"
	>
		<CheckCircle class="w-4 h-4 shrink-0" />
		{$t('storage.removal.finished', { device: status.device ?? '' })}
	</div>
{:else if part === 'result' && mounted && !error && status?.status !== 'running' && status?.result === 'failed'}
	<Alert variant="error" size="sm" title={$t('storage.removal.failed', { device: status.device ?? '' })}>
		<span class="break-words">{status.error}</span>
	</Alert>
{/if}
