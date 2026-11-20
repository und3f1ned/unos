<script lang="ts">
	// An app of the App Store's catalog: its source, category and hardware, and, once installed,
	// its state; a click (or Enter) opens its details, where it is installed. The card is the one
	// control: no Install button of its own, so that the catalog's 69 cards carry no 69 primary
	// buttons and no button sits inside a button; its foot says what a click leads to
	// (webui.ux-apps). Opening an installed app is its card's on the Installed tab (ADR-0431).
	import { BadgeCheck, ChevronRight, Cpu } from 'lucide-svelte';
	import { Badge } from '$lib/components/ui';
	import { allProfiles, missingHardware, type AppTemplate, type InstalledApp } from '$lib/api/apps';
	import { t } from '$lib/i18n';
	import AppCategory from './AppCategory.svelte';
	import { stateOf } from './installed';
	import { sourceBadge } from './labels';

	type Props = {
		app: AppTemplate;
		/** Apps can be installed now: Docker answered. */
		canInstall: boolean;
		/** Its installed instance, if any. */
		installedInfo: InstalledApp | undefined;
		/** Its install runs. */
		installing: boolean;
		onopen: (app: AppTemplate) => void;
	};

	let { app, canInstall, installedInfo, installing, onopen }: Props = $props();

	const source = $derived(sourceBadge(app.source, false));
</script>

<div
	class="card flex h-full flex-col hover:bg-surface-overlay transition-colors cursor-pointer"
	onclick={() => onopen(app)}
	role="button"
	tabindex="0"
	onkeypress={(e) => e.key === 'Enter' && onopen(app)}
>
	<div class="flex items-start gap-3 mb-3">
		<!-- rounded-xl, 12 px: appstore.spec.ts finds the catalog's tiles by it. -->
		<div
			class="w-10 h-10 rounded-xl bg-surface-overlay flex items-center justify-center text-xl flex-shrink-0"
			aria-hidden="true"
		>
			{app.icon}
		</div>
		<div class="flex-1 min-w-0">
			<h3 class="font-semibold truncate mb-1">{app.name}</h3>
			<div class="flex flex-wrap items-center gap-1.5">
				<Badge variant="neutral">
					{#if source.verified}<BadgeCheck class="w-3 h-3" aria-hidden="true" />{/if}
					{$t(source.key)}
				</Badge>
				<Badge variant="neutral"><AppCategory category={app.category} /></Badge>
				{#if installedInfo && missingHardware(app, installedInfo).length > 0}
					<!-- A container of it did not get the hardware: its details say how to give it. -->
					<Badge variant="warning">
						<Cpu class="w-3 h-3" aria-hidden="true" />
						{$t('apps.hardware.badgeMissing')}
					</Badge>
				{:else if allProfiles(app).length > 0}
					<Badge variant="neutral">
						<Cpu class="w-3 h-3" aria-hidden="true" />
						{$t('apps.hardware.badge')}
					</Badge>
				{/if}
			</div>
		</div>
	</div>
	<p class="text-sm text-content-secondary line-clamp-2 mb-3">{app.description}</p>
	<div class="mt-auto flex items-center justify-between gap-2 pt-3 border-t border-border min-h-[2.5rem]">
		<span class="mono-xs text-content-muted truncate">{app.version || ''}</span>
		{#if installedInfo}
			{@const state = stateOf(installedInfo)}
			<Badge variant={state.variant} dot>{$t(state.key)}</Badge>
		{:else if installing}
			<Badge variant="info" dot>{$t('apps.job.state.running')}</Badge>
		{:else if !app.available}
			<span class="text-xs text-content-muted text-right">
				{$t('apps.notAvailable', { platform: app.host_platform ?? '' })}
			</span>
		{:else if canInstall}
			<span class="inline-flex items-center gap-0.5 text-sm font-medium text-content-link" data-install>
				{$t('apps.install')}
				<ChevronRight class="w-4 h-4" aria-hidden="true" />
			</span>
		{/if}
	</div>
</div>
