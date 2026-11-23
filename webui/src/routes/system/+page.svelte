<script lang="ts">
	import { getSystemInfo, type DockerData, type SystemInfo } from '$lib/api/system';
	import { Card, ErrorState, Skeleton } from '$lib/components/ui';
	import { messageOf } from '$lib/errors';
	import { HostSettings, HostSettingsCard } from '$lib/components/system/general';
	import { formatDuration, secondsSince } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { followPage } from '$lib/pages/follow.svelte';
	import { errorOf, valueOr } from '$lib/pages/read';
	import type { GeneralSnapshot } from '$lib/pages/system/general';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// System > General (ADR-0431): the facts of the host and its name and time zone. The host name is
	// shown by its field alone, the version by Updates, the signed-in account by the sidebar.
	let systemInfo = $state<SystemInfo | null>(null);
	// The tab's data have come (its load, ADR-0469): until then, on the tab's first page, its skeleton.
	let loaded = $state(false);
	// Why the system information could not be read.
	let infoError = $state('');
	// Where Docker keeps its data (ADR-0487), and why that could not be read.
	let dockerData = $state<DockerData | null>(null);
	let dockerError = $state('');

	// The name and time zone of the host (ADR-0331).
	const host = new HostSettings();

	followPage(
		() => data,
		(snapshot) => apply(snapshot)
	);

	function apply(snapshot: GeneralSnapshot) {
		systemInfo = valueOr(snapshot.info, null);
		infoError = errorOf(snapshot.info);
		dockerData = valueOr(snapshot.docker, null);
		dockerError = errorOf(snapshot.docker);
		host.apply(snapshot.hostname, snapshot.zone, snapshot.zones);
		loaded = true;
	}

	/** Retry of the system information. */
	async function loadInfo() {
		try {
			systemInfo = await getSystemInfo();
			infoError = '';
		} catch (e) {
			infoError = messageOf(e);
		}
	}

	// The pool that holds Docker's data, a move to one, or neither (ADR-0487).
	let dockerPlace = $derived(
		!dockerData
			? $t('general.info.dockerUnknown')
			: dockerData.moving_to
				? $t('general.info.dockerMoving', { pool: dockerData.moving_to })
				: dockerData.pool
					? $t('general.info.dockerPool', { pool: dockerData.pool })
					: $t('general.info.dockerNoPool')
	);

	// Reactive, so that the uptime follows a change of the language.
	let formatUptime = $derived((bootTime: string): string => {
		const seconds = secondsSince(bootTime);
		return seconds === null ? bootTime : formatDuration(seconds, $intlLocale, $t, { parts: 3 });
	});
</script>

<!-- A fact as label over value (.kv, ADR-0428). -->
{#snippet info(label: string, value: string, valueClass: string)}
	<div>
		<span class="kv-label">{label}</span>
		<span class="kv-value {valueClass}">{value}</span>
	</div>
{/snippet}

<div class="space-y-4">
	<h2 class="text-xl font-semibold">{$t('general.title')}</h2>

	{#if !loaded}
		<!-- The tab's first page while its data take longer than a moment (ADR-0469): the two cards in
		     their places, swapped for them at once. -->
		<div class="space-y-4" aria-busy="true">
			<div class="card">
				<Skeleton size="base" class="w-40 mb-4" />
				<div class="kv">
					{#each [0, 1, 2, 3, 4] as fact (fact)}
						<div>
							<Skeleton size="xs" class="w-16" />
							<Skeleton class="w-28" />
						</div>
					{/each}
				</div>
			</div>
			<div class="card">
				<Skeleton size="base" class="w-40 mb-4" />
				<div class="space-y-4">
					{#each [0, 1] as field (field)}
						<div>
							<Skeleton size="xs" class="w-24 mb-1" />
							<Skeleton shape="block" class="h-9 w-full" />
							<Skeleton size="xs" class="mt-1 w-2/3" />
						</div>
					{/each}
				</div>
			</div>
		</div>
	{:else}
		<!-- System Info -->
		<Card title={$t('general.info.title')}>
			{#if systemInfo}
				<div class="kv">
					{@render info($t('general.info.os'), systemInfo.os, '')}
					{@render info($t('general.info.kernel'), systemInfo.kernel, 'font-mono text-xs')}
					{@render info($t('general.info.architecture'), systemInfo.architecture, '')}
					{@render info($t('general.info.uptime'), formatUptime(systemInfo.boot_time), 'tabular')}
					<!-- Where Docker keeps its data: its pool, with the data root's path; why it is not known. -->
					<div data-testid="docker-data">
						<span class="kv-label">{$t('general.info.docker')}</span>
						<span class="kv-value">{dockerPlace}</span>
						{#if dockerData}
							<span class="block mono-xs text-muted break-all">{dockerData.data_root}</span>
						{:else if dockerError}
							<span class="block text-xs text-muted">{dockerError}</span>
						{/if}
					</div>
				</div>
			{:else}
				<ErrorState title={$t('general.info.loadFailed')} message={infoError} onretry={loadInfo} />
			{/if}
		</Card>

		<HostSettingsCard {host} />
	{/if}
</div>
