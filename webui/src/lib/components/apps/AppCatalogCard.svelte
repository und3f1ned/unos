<script lang="ts">
	// An app of the App Store's catalog: its source, category and hardware, and, once installed,
	// its state and address; a click (or Enter) opens its details, where it is installed.
	import { Cpu, Download, ExternalLink } from 'lucide-svelte';
	import { Badge, Button } from '$lib/components/ui';
	import { allProfiles, missingHardware, type AppTemplate, type InstalledApp } from '$lib/api/apps';
	import { t } from '$lib/i18n';
	import AppCategory from './AppCategory.svelte';
	import AppRunningDot from './AppRunningDot.svelte';
	import { stateOf, webUrl } from './installed';
	import { sourceBadge } from './labels';

	type Props = {
		app: AppTemplate;
		/** Its installed instance, if any. */
		installedInfo: InstalledApp | undefined;
		/** Its install runs: the card offers no Install. */
		installing: boolean;
		onopen: (app: AppTemplate) => void;
	};

	let { app, installedInfo, installing, onopen }: Props = $props();

	const source = $derived(sourceBadge(app.source, false));
</script>

<div
	class="card hover:bg-surface-overlay transition-colors cursor-pointer"
	onclick={() => onopen(app)}
	role="button"
	tabindex="0"
	onkeypress={(e) => e.key === 'Enter' && onopen(app)}
>
	<div class="flex items-start gap-3 mb-3">
		<div
			class="w-10 h-10 rounded-card bg-surface-overlay flex items-center justify-center text-xl flex-shrink-0"
		>
			{app.icon}
		</div>
		<div class="flex-1 min-w-0">
			<div class="flex items-center gap-2 mb-0.5">
				<h3 class="font-semibold truncate">{app.name}</h3>
				{#if installedInfo}
					<AppRunningDot running={installedInfo.status === 'running'} />
				{/if}
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<Badge variant={source.variant} class="text-xs">{$t(source.key)}</Badge>
				<span class="text-xs text-muted"><AppCategory category={app.category} /></span>
				{#if installedInfo && missingHardware(app, installedInfo).length > 0}
					<!-- A container of it did not get the hardware: its details say how to give it. -->
					<Badge variant="warning" class="text-xs">
						<Cpu class="w-3 h-3 mr-1" />
						{$t('apps.hardware.badgeMissing')}
					</Badge>
				{:else if allProfiles(app).length > 0}
					<Badge variant="info" class="text-xs">
						<Cpu class="w-3 h-3 mr-1" />
						{$t('apps.hardware.badge')}
					</Badge>
				{/if}
			</div>
		</div>
	</div>
	<p class="text-sm text-secondary line-clamp-2 mb-3">{app.description}</p>
	<div class="flex items-center justify-between pt-3 border-t border-border">
		{#if installedInfo}
			{@const state = stateOf(installedInfo)}
			{@const href = webUrl(installedInfo)}
			<Badge variant={state.variant}>{$t(state.key)}</Badge>
			<div class="flex items-center gap-1">
				{#if href && installedInfo.missing.length === 0}
					<a
						{href}
						target="_blank"
						rel="noopener"
						onclick={(e) => e.stopPropagation()}
						class="p-1.5 rounded hover:bg-surface-muted text-info transition-colors"
						title={$t('apps.open')}
					>
						<ExternalLink class="w-4 h-4" />
					</a>
				{/if}
			</div>
		{:else if installing}
			<span class="text-xs text-muted mono">{app.version || ''}</span>
			<Badge variant="info">{$t('apps.job.state.running')}</Badge>
		{:else if !app.available}
			<Badge variant="warning" class="text-xs">
				{$t('apps.notAvailable', { platform: app.host_platform ?? '' })}
			</Badge>
			<Button variant="primary" size="sm" icon={Download} disabled>{$t('apps.install')}</Button>
		{:else}
			<span class="text-xs text-muted mono">{app.version || ''}</span>
			<Button variant="primary" size="sm" icon={Download}>{$t('apps.install')}</Button>
		{/if}
	</div>
</div>
