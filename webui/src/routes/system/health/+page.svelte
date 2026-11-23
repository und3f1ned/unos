<script lang="ts">
	import { replaceSoon, type DiskPrediction, type SmartInfo, type SystemDiskHealth } from '$lib/api/health';
	import { Alert, Badge, Button, EmptyState, ErrorState, Skeleton, TechDetails } from '$lib/components/ui';
	import { SmartDataDialog, SystemDiskCard } from '$lib/components/system/health';
	import {
		RECOMMENDATION,
		capacityText,
		diskState,
		orUnknown,
		predictionOf,
		textOf
	} from '$lib/components/system/health/texts';
	import { formatTemperature } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { HardDrive, Thermometer, RefreshCw } from 'lucide-svelte';
	import { followPage } from '$lib/pages/follow.svelte';
	import { refreshPage } from '$lib/pages/load';
	import { healthReader, type HealthSnapshot } from '$lib/pages/system/health';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let disks = $state<SmartInfo[]>([]);
	// The predictions that say replace soon (ADR-0235).
	let toReplace = $state<DiskPrediction[]>([]);
	// A refresh is under way: it keeps what is shown.
	let loading = $state(false);
	// The tab's data have come (its load, ADR-0469): until then, on the tab's first page, its skeleton.
	let loaded = $state(false);
	let error = $state('');
	let predictionsError = $state('');
	// The system disk's wear and errors when it is an eMMC (ADR-0471): a card of its own after the list.
	let systemDisk = $state<SystemDiskHealth | null>(null);
	let systemDiskError = $state('');
	// The disk whose SMART data the dialog shows: the data of GET /health/disks,
	// so that opening a disk runs no smartctl.
	let openDisk = $state<SmartInfo | null>(null);

	followPage(
		() => data,
		(snapshot) => apply(snapshot)
	);

	function apply(snapshot: HealthSnapshot) {
		if (snapshot.disks.ok) {
			disks = snapshot.disks.value;
			error = '';
		} else {
			// For example 500 "Could not list the host's disks: ..." when lsblk
			// fails: say so instead of "No disks found", and drop the old list.
			disks = [];
			error = snapshot.disks.error || $t('health.loadError');
		}
		if (snapshot.predictions.ok) {
			toReplace = replaceSoon(snapshot.predictions.value);
			predictionsError = '';
		} else {
			// Without the predictions no disk is marked: say so, so that a disk to
			// replace is not taken for one without a mark.
			toReplace = [];
			predictionsError = snapshot.predictions.error || $t('health.predictionsError');
		}
		if (snapshot.systemDisk.ok) {
			systemDisk = snapshot.systemDisk.value;
			systemDiskError = '';
		} else {
			systemDisk = null;
			systemDiskError = snapshot.systemDisk.error || $t('health.systemDisk.loadError');
		}
		loaded = true;
	}

	/** Every disk read again: the tab's load runs again, and the tab keeps what it shows meanwhile. */
	async function loadHealth() {
		loading = true;
		await refreshPage(healthReader.key);
		loading = false;
	}

	// The predictions to replace soon that match no listed disk: smartctl could
	// not read the disk on this load (its serial shows as Unknown), or another
	// disk took its place. They are listed apart, as on the dashboard.
	let unmatched = $derived(toReplace.filter((prediction) => !disks.some((disk) => disk.serial === prediction.serial)));
</script>

<!-- One badge per disk, the plan in plain words, and smartctl's attributes behind Why and in the
     SMART data dialog: no health shown three ways. -->
<div class="space-y-4">
	<div class="flex items-center justify-between gap-3">
		<h2 class="text-xl font-semibold">{$t('health.title')}</h2>
		<Button variant="ghost" size="icon" icon={RefreshCw} {loading} title={$t('health.refresh')} onclick={loadHealth}>
			<span class="sr-only">{$t('health.refresh')}</span>
		</Button>
	</div>

	{#if !loaded}
		<!-- The list's shape (ADR-0430): one flush card of divided rows, each the icon, the model over
         the device, and the badge and button on the right. -->
		<div class="card card-flush divide-y divide-border" aria-busy="true">
			{#each [0, 1, 2] as row (row)}
				<div class="flex items-center justify-between gap-4 px-5 py-4">
					<div class="flex min-w-0 flex-1 items-center gap-3">
						<Skeleton shape="block" class="h-5 w-5 shrink-0 rounded" />
						<div class="min-w-0 flex-1">
							<Skeleton size="base" class="w-48 max-w-full" />
							<Skeleton class="w-32 max-w-full" />
						</div>
					</div>
					<Skeleton shape="block" class="h-8 w-40 shrink-0 rounded" />
				</div>
			{/each}
		</div>
	{:else if error}
		<div class="card">
			<ErrorState title={$t('health.loadError')} message={error} onretry={loadHealth} retrying={loading} />
		</div>
	{:else if disks.length === 0}
		<div class="card">
			<EmptyState compact icon={HardDrive} title={$t('health.noDisks')} description={$t('health.noDisksHint')} />
		</div>
	{:else}
		{#if predictionsError}
			<Alert variant="warning" size="sm" title={$t('health.predictionsError')}>{predictionsError}</Alert>
		{/if}
		{#if unmatched.length > 0}
			<Alert variant="error" title={$t('health.replaceSoon')}>
				<p class="text-sm">{$t('health.unmatched')}</p>
				<ul class="mt-2 space-y-1 text-sm">
					{#each unmatched as prediction}
						<li>
							<span class="mono">{prediction.device}</span>
							({$t('health.serialOf', { serial: prediction.serial })}): {prediction.risk_factors.join('; ')}
						</li>
					{/each}
				</ul>
			</Alert>
		{/if}
		<ul class="card card-flush divide-y divide-border" aria-label={$t('health.disks')}>
			{#each disks as disk}
				{@const prediction = predictionOf(disk, toReplace)}
				{@const mark = diskState(disk, prediction)}
				<li class="px-5 py-4">
					<div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
						<div class="flex min-w-0 items-center gap-3">
							<HardDrive class="h-5 w-5 shrink-0 text-content-secondary" aria-hidden="true" />
							<div class="min-w-0">
								<p class="font-medium break-words">{orUnknown($t, disk.model)}</p>
								<p class="text-sm text-content-secondary">
									<span class="mono">{disk.device}</span> · {capacityText($t, $intlLocale, disk.capacity)}
								</p>
							</div>
						</div>
						<div class="flex flex-wrap items-center gap-3">
							{#if disk.temperature !== null}
								<span class="flex items-center gap-1 text-sm text-content-secondary tabular">
									<Thermometer class="h-4 w-4" aria-hidden="true" />
									{formatTemperature(disk.temperature, $intlLocale)}
								</span>
							{/if}
							<Badge variant={mark.variant} dot={mark.variant !== 'neutral'}>{$t(mark.key)}</Badge>
							<Button variant="secondary" size="sm" onclick={() => (openDisk = disk)}>
								{$t('health.smartData')}<span class="sr-only">{$t('health.smartDataOf', { device: disk.device })}</span>
							</Button>
						</div>
					</div>
					{#if prediction}
						<div class="mt-2 pl-8 text-sm">
							<p class="text-content">{textOf($t, RECOMMENDATION, prediction.recommendation)}</p>
							<TechDetails summary={$t('health.why')}>
								<ul class="list-disc pl-5 text-[13px] text-content-secondary">
									{#each prediction.risk_factors as factor}
										<li class="break-words">{factor}</li>
									{/each}
								</ul>
							</TechDetails>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	{#if loaded}
		<SystemDiskCard health={systemDisk} error={systemDiskError} />
	{/if}
</div>

<SmartDataDialog disk={openDisk} onclose={() => (openDisk = null)} />
