<script lang="ts">
	// The Volumes tab of an app's details. An installed app lists the folders its containers
	// really use; the install form lists each container's folders on the chosen pool or on the
	// system disk, and lets the main container's data folders move (a password file never,
	// ADR-0381).
	import { Badge, Input } from '$lib/components/ui';
	import {
		poolHostPath,
		type AppContainer,
		type AppPool,
		type AppTemplate,
		type AppVolume,
		type InstalledApp,
		type VolumeMapping
	} from '$lib/api/apps';
	import { t } from '$lib/i18n';
	import AppContainerGroup from './AppContainerGroup.svelte';
	import { effectiveHostPath, isGeneratedSecret } from './detail';

	type Props = {
		app: AppTemplate;
		isInstalled: boolean;
		installedApp: InstalledApp | null;
		/** The installed app's folders per container. */
		installedVolumes: { name: string; volumes: AppVolume[] }[];
		/** The app's containers, its main container first. */
		containers: AppContainer[];
		several: boolean;
		needsPool: boolean;
		selectedPool: AppPool | null;
		/** The host folders that the form moved, by container path. */
		volumeOverrides: Record<string, string>;
		/** The container paths whose moved folder is not inside a pool. */
		invalidVolumes: string[];
		/** A data folder's host path was typed. */
		onpath: (volume: VolumeMapping, value: string) => void;
	};

	let {
		app,
		isInstalled,
		installedApp,
		installedVolumes,
		containers,
		several,
		needsPool,
		selectedPool,
		volumeOverrides,
		invalidVolumes,
		onpath
	}: Props = $props();

	function inputValue(event: Event): string {
		return (event.target as HTMLInputElement).value;
	}
</script>

<!-- The container's side of a folder, and whether it can write there. -->
{#snippet containerPath(path: string)}
	<span class="text-secondary pt-8">→</span>
	<div class="flex-1 min-w-0">
		<p class="form-label">{$t('apps.detail.containerPath')}</p>
		<p class="mono text-sm break-all py-2">{path}</p>
	</div>
{/snippet}

{#snippet access(readonly: boolean)}
	{#if readonly}
		<Badge variant="warning">{$t('apps.detail.readOnly')}</Badge>
	{:else}
		<Badge variant="outline">{$t('apps.detail.readWrite')}</Badge>
	{/if}
{/snippet}

{#snippet secretNote()}
	<p class="text-xs text-muted mt-2">{$t('apps.detail.generatedSecret')}</p>
{/snippet}

{#if isInstalled}
	<!-- The folders the installed containers really use, not paths on a pool -->
	<div class="space-y-3">
		<p class="text-sm text-secondary mb-4">{$t('apps.detail.installedVolumesHint')}</p>
		{#if installedApp && installedVolumes.length > 0}
			{#each installedVolumes as group (group.name)}
				<!-- An app of several containers groups the folders per container (ADR-0381). -->
				<AppContainerGroup name={group.name} kind="volumes" {several}>
					{#each group.volumes as volume}
						<div class="card-nested">
							<div class="flex items-start gap-4">
								<div class="flex-1 min-w-0">
									<p class="form-label">{$t('apps.detail.hostPath')}</p>
									<p class="mono text-sm break-all py-2">{volume.host}</p>
								</div>
								{@render containerPath(volume.container)}
								<div class="pt-8 flex flex-col items-end gap-1">
									{@render access(volume.readonly)}
									{#if installedApp.data_outside_pools.includes(volume.host)}
										<Badge variant="error">{$t('apps.detail.notOnPool')}</Badge>
									{/if}
								</div>
							</div>
							{#if isGeneratedSecret(app, volume)}
								{@render secretNote()}
							{/if}
						</div>
					{/each}
				</AppContainerGroup>
			{/each}
		{:else}
			<p class="text-sm text-muted">{$t('apps.detail.noVolumes')}</p>
		{/if}
	</div>
{:else}
	<div class="space-y-3">
		<p class="text-sm text-secondary mb-4">
			{#if needsPool}
				{$t('apps.detail.volumesPoolHint')}
			{/if}
			{$t('apps.detail.volumesSystemHint')}
		</p>
		{#each containers as container (container.name)}
			{#if container.spec.volumes.length > 0}
				<!-- An app of several containers groups the folders per container; only the main
				     container's data folders can be moved, a password file never (ADR-0381). -->
				<AppContainerGroup name={container.name} kind="volumes" {several}>
					{#each container.spec.volumes as volume}
						<div class="card-nested">
							<div class="flex items-start gap-4">
								<div class="flex-1 min-w-0">
									{#if volume.on_pool && container.companion === null && !volume.generated_secret}
										<Input
											class="font-mono text-sm"
											label={$t('apps.detail.hostPath')}
											value={effectiveHostPath(volume, selectedPool, volumeOverrides)}
											oninput={(e) => onpath(volume, inputValue(e))}
											disabled={isInstalled || selectedPool === null}
											autocomplete="off"
											error={invalidVolumes.includes(volume.container)
												? $t('apps.detail.insidePool')
												: ''}
										/>
									{:else}
										<p class="form-label">{$t('apps.detail.hostPath')}</p>
										<p class="mono text-sm break-all py-2">
											{volume.on_pool && selectedPool ? poolHostPath(volume, selectedPool.path) : volume.host}
										</p>
									{/if}
								</div>
								{@render containerPath(volume.container)}
								<div class="pt-8 flex flex-col items-end gap-1">
									{@render access(volume.readonly)}
									{#if !volume.on_pool}
										<Badge variant="info">{$t('apps.detail.system')}</Badge>
									{/if}
									{#if volume.is_file}
										<Badge variant="outline">{$t('apps.detail.file')}</Badge>
									{/if}
								</div>
							</div>
							{#if volume.generated_secret}
								{@render secretNote()}
							{/if}
						</div>
					{/each}
				</AppContainerGroup>
			{/if}
		{/each}
	</div>
{/if}
