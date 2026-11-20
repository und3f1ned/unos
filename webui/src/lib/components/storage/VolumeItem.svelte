<script lang="ts">
	// A volume of the Volumes dialog: its name and path, the use of its quota (ADR-0243), and its use
	// over time when it is opened.
	import { LineChart, Trash2 } from 'lucide-svelte';
	import { Alert, Button, ProgressBar, Skeleton } from '$lib/components/ui';
	import type { Volume } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { quotaVariant } from './pools';
	import type { PoolVolumes } from './volumes.svelte';
	import VolumeUsageChart from './VolumeUsageChart.svelte';

	type Props = {
		volume: Volume;
		/** The dialog's volumes, whose chart and delete buttons this volume's act on. */
		volumes: PoolVolumes;
		formatBytes: (bytes: number) => string;
		percentFormat: Intl.NumberFormat;
	};

	let { volume, volumes, formatBytes, percentFormat }: Props = $props();

	const usedOfQuota = (volume: Volume): string =>
		volume.used === null || volume.quota === null
			? ''
			: $t('storage.volumes.usedOfQuota', {
					used: formatBytes(volume.used),
					quota: formatBytes(volume.quota),
					percent: percentFormat.format(volume.quota > 0 ? volume.used / volume.quota : 1)
				});
</script>

<li class="card-nested space-y-2">
	<div class="flex items-center justify-between gap-2">
		<div class="min-w-0">
			<p class="font-semibold break-all">{volume.name}</p>
			<p class="mono-xs text-muted break-all">{volume.path}</p>
		</div>
		<div class="flex flex-shrink-0 items-center gap-1">
			{#if volume.used !== null}
				<Button
					variant="ghost"
					size="icon-sm"
					pressed={volumes.usageOf === volume.name}
					onclick={() => volumes.toggleUsage(volume)}
				>
					<LineChart class="w-4 h-4" />
					<span class="sr-only">{$t('storage.volumes.chartShow', { name: volume.name })}</span>
				</Button>
			{/if}
			<Button
				variant="ghost-danger"
				size="icon-sm"
				disabled={volumes.busy || volumes.readOnly}
				onclick={() => volumes.askDelete(volume)}
			>
				<Trash2 class="w-4 h-4" />
				<span class="sr-only">{$t('storage.volumes.deleteOne', { name: volume.name })}</span>
			</Button>
		</div>
	</div>
	{#if volume.used !== null && volume.quota !== null}
		<ProgressBar
			value={volume.quota === 0 ? 1 : volume.used}
			max={volume.quota === 0 ? 1 : volume.quota}
			size="sm"
			variant={quotaVariant(volume)}
			label={$t('storage.volumes.useOf', { name: volume.name })}
		/>
		<p class="text-xs text-secondary">{usedOfQuota(volume)}</p>
	{:else if volume.used !== null}
		<p class="text-xs text-secondary">
			{$t('storage.volumes.usedNoQuota', { used: formatBytes(volume.used) })}
		</p>
	{:else if volume.usage === 'unknown'}
		<p class="text-xs text-muted">{$t('storage.volumes.useUnknown')}</p>
	{/if}
	{#if volumes.usageOf === volume.name}
		<div class="space-y-1">
			<p class="text-xs font-medium text-secondary">{$t('storage.volumes.chartTitle')}</p>
			{#if volumes.usageLoading}
				<div aria-busy="true"><Skeleton shape="block" class="h-32 w-full" /></div>
			{:else if volumes.usageError}
				<Alert variant="error" title={$t('storage.volumes.chartError')}>{volumes.usageError}</Alert>
			{:else}
				<VolumeUsageChart points={volumes.usagePoints} {formatBytes} />
			{/if}
		</div>
	{/if}
</li>
