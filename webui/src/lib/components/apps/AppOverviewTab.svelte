<script lang="ts">
	// The Overview tab of an app's details: its containers and images (one image for an app of
	// one container), what its images take on the system disk, where its data goes on the chosen
	// pool, its memory needs, and its counts of ports, folders and variables.
	import { Database, HardDrive, Network, Settings } from 'lucide-svelte';
	import { Alert, type IconComponent } from '$lib/components/ui';
	import type { AppContainer, AppPool, AppTemplate } from '$lib/api/apps';
	import { intlLocale, t } from '$lib/i18n';
	import { DISK_AWAKE, type DataLocation } from './detail';
	import { formatSize } from './size';

	type Props = {
		app: AppTemplate;
		/** The app's containers, its main container first. */
		containers: AppContainer[];
		several: boolean;
		isInstalled: boolean;
		needsPool: boolean;
		selectedPool: AppPool | null;
		/** Where the app's data goes on the selected pool. */
		dataLocations: DataLocation[];
		volumeCount: number;
		variableCount: number;
	};

	let {
		app,
		containers,
		several,
		isInstalled,
		needsPool,
		selectedPool,
		dataLocations,
		volumeCount,
		variableCount
	}: Props = $props();
</script>

<!-- What the app's images take on the system disk. -->
{#snippet imagesSize(className: string)}
	{#if app.system_disk_bytes}
		<p class={className}>
			{$t('apps.detail.imagesSize', { name: app.name, size: formatSize(app.system_disk_bytes, $intlLocale, $t, 2) })}
		</p>
	{/if}
{/snippet}

{#snippet count(Icon: IconComponent, iconClass: string, n: number, key: string)}
	<div class="card-nested text-center">
		<Icon class={iconClass} />
		<p class="text-2xl font-bold">{n}</p>
		<p class="text-xs text-secondary">{$t(key)}</p>
	</div>
{/snippet}

<div class="space-y-4">
	{#if several}
		<!-- Each container with the image it is created from (ADR-0381); the images break on a phone. -->
		<div class="card-nested">
			<h4 class="text-sm font-medium mb-1">{$t('apps.detail.containers')}</h4>
			<p class="text-xs text-muted mb-3">
				{$t('apps.detail.containersHint', { name: app.name, count: containers.length })}
			</p>
			<ul class="space-y-2">
				{#each containers as container (container.name)}
					<li class="text-sm min-w-0">
						<p class="mono break-all">{container.name}</p>
						<code class="mono text-xs text-muted break-all">{container.spec.image}</code>
					</li>
				{/each}
			</ul>
			{@render imagesSize('text-xs text-muted mt-3')}
		</div>
	{:else}
		<!-- Docker Image: on a phone it goes below its label and breaks (ADR-0302). -->
		<div class="card-nested">
			<div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
				<span class="text-secondary text-sm">{$t('apps.detail.image')}</span>
				<code class="mono text-sm min-w-0 break-all">{app.image}</code>
			</div>
			{@render imagesSize('text-xs text-muted mt-2')}
		</div>
	{/if}

	{#if DISK_AWAKE.has(app.id)}
		<Alert variant="info">{$t('apps.detail.diskAwake', { name: app.name })}</Alert>
	{/if}

	<!-- Where the data goes on the selected pool -->
	{#if needsPool && selectedPool && !isInstalled}
		<div class="card-nested">
			<div class="flex items-center gap-2 mb-3">
				<Database class="w-4 h-4 icon-storage" />
				<h4 class="text-sm font-medium">
					{$t('apps.detail.dataLocations', { pool: selectedPool.name })}
				</h4>
			</div>
			<ul class="space-y-2">
				{#each dataLocations as location (location.host)}
					<li class="text-sm">
						<p class="mono break-all">{location.host}</p>
						{#each location.mounts as mount}
							<p class="text-xs text-muted break-all">
								{#if several}
									{$t(mount.readonly ? 'apps.detail.mountedAtInReadOnly' : 'apps.detail.mountedAtIn', {
										path: mount.path,
										container: mount.container
									})}
								{:else}
									{$t(mount.readonly ? 'apps.detail.mountedAtReadOnly' : 'apps.detail.mountedAt', {
										path: mount.path
									})}
								{/if}
							</p>
						{/each}
						{#if location.secret}
							<p class="text-xs text-muted">{$t('apps.detail.generatedSecret')}</p>
						{/if}
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	<!-- Resource Recommendations -->
	{#if app.min_memory || app.recommended_memory}
		<div class="card-nested">
			<h4 class="text-sm font-medium mb-3">{$t('apps.detail.resources')}</h4>
			<div class="grid grid-cols-2 gap-4">
				{#if app.min_memory}
					<div>
						<span class="text-secondary text-sm">{$t('apps.detail.minMemory')}</span>
						<p class="font-medium">{app.min_memory}</p>
					</div>
				{/if}
				{#if app.recommended_memory}
					<div>
						<span class="text-secondary text-sm">{$t('apps.detail.recommendedMemory')}</span>
						<p class="font-medium">{app.recommended_memory}</p>
					</div>
				{/if}
			</div>
		</div>
	{/if}

	<!-- Quick Summary -->
	<div class="grid grid-cols-3 gap-4">
		{@render count(Network, 'w-5 h-5 mx-auto mb-1 text-info', app.ports?.length || 0, 'apps.detail.ports')}
		{@render count(HardDrive, 'w-5 h-5 mx-auto mb-1 text-accent-memory', volumeCount, 'apps.detail.volumes')}
		{@render count(Settings, 'w-5 h-5 mx-auto mb-1 text-accent-temperature', variableCount, 'apps.detail.variables')}
	</div>
</div>
