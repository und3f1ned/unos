<script lang="ts">
	// The Overview tab of an app's details. What a person decides by comes first: the memory it
	// needs (in the page's units, not Docker's "6g"), what its images take on the system disk, how
	// many containers it runs, and where its data goes on the chosen pool; its containers and the
	// images they are made from close the tab, the images' pins as small print. The counts of its
	// ports, folders and variables are its tabs' (webui.ux-apps).
	import { Database } from 'lucide-svelte';
	import { Alert } from '$lib/components/ui';
	import type { AppContainer, AppPool, AppTemplate } from '$lib/api/apps';
	import { intlLocale, t } from '$lib/i18n';
	import { DISK_AWAKE, memoryBytes, type DataLocation } from './detail';
	import { formatSize } from '$lib/format';

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
	};

	let { app, containers, several, isInstalled, needsPool, selectedPool, dataLocations }: Props = $props();

	/** A memory size of the catalog in the page's units; one that is no size as it comes. */
	function memory(value: string): string {
		const bytes = memoryBytes(value);
		return bytes === null ? value : formatSize(bytes, $intlLocale, $t);
	}
</script>

<div class="space-y-5">
	<!-- At a glance -->
	{#if app.recommended_memory || app.min_memory || app.system_disk_bytes || several}
		<div class="kv">
			{#if app.recommended_memory}
				<div>
					<span class="kv-label">{$t('apps.detail.recommendedMemory')}</span>
					<span class="kv-value" data-field="recommended-memory">{memory(app.recommended_memory)}</span>
				</div>
			{/if}
			{#if app.min_memory}
				<div>
					<span class="kv-label">{$t('apps.detail.minMemory')}</span>
					<span class="kv-value" data-field="min-memory">{memory(app.min_memory)}</span>
				</div>
			{/if}
			{#if app.system_disk_bytes}
				<div>
					<span class="kv-label">{$t('apps.detail.imagesDisk')}</span>
					<span class="kv-value">
						{$t('apps.detail.imagesSize', { size: formatSize(app.system_disk_bytes, $intlLocale, $t, { digits: 2 }) })}
					</span>
				</div>
			{/if}
			{#if several}
				<div>
					<span class="kv-label">{$t('apps.detail.containers')}</span>
					<span class="kv-value">{$t('apps.detail.containerCount', { n: containers.length })}</span>
				</div>
			{/if}
		</div>
	{/if}

	{#if DISK_AWAKE.has(app.id)}
		<Alert variant="info">{$t('apps.detail.diskAwake', { name: app.name })}</Alert>
	{/if}

	<!-- Where the data goes on the selected pool -->
	{#if needsPool && selectedPool && !isInstalled}
		<section>
			<h4 class="flex items-center gap-2 text-sm font-medium mb-2">
				<Database class="w-4 h-4 icon-storage" aria-hidden="true" />
				{$t('apps.detail.dataLocations', { pool: selectedPool.name })}
			</h4>
			<ul class="space-y-2">
				{#each dataLocations as location (location.host)}
					<li class="text-sm">
						<p class="mono break-all">{location.host}</p>
						{#each location.mounts as mount}
							<p class="text-xs text-content-muted break-all">
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
							<p class="text-xs text-content-muted">{$t('apps.detail.generatedSecret')}</p>
						{/if}
					</li>
				{/each}
			</ul>
		</section>
	{/if}

	<!-- Its containers and their images, last: the images break on a phone (ADR-0302). -->
	<section class="pt-4 border-t border-border">
		{#if several}
			<h4 class="text-sm font-medium mb-1">{$t('apps.detail.containersAndImages')}</h4>
			<p class="text-xs text-content-muted mb-3">
				{$t('apps.detail.containersHint', { name: app.name, count: containers.length })}
			</p>
			<ul class="space-y-2">
				{#each containers as container (container.name)}
					<li class="text-sm min-w-0">
						<p class="mono break-all">{container.name}</p>
						<code class="mono text-xs text-content-muted break-all">{container.spec.image}</code>
					</li>
				{/each}
			</ul>
		{:else}
			<h4 class="text-sm font-medium mb-1">{$t('apps.detail.image')}</h4>
			<code class="mono text-xs text-content-muted break-all">{app.image}</code>
		{/if}
	</section>
</div>
