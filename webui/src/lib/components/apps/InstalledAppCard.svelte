<script lang="ts">
	// An installed app on the App Store's Installed tab: its state and health, port, pool, group
	// and containers, its buttons, what Docker lacks or does not run, the earlier version that its
	// last update kept (ADR-0408), its data outside the pools, and its containers' resources.
	import { ArrowUpCircle, ExternalLink, Play, RotateCw, Square, Trash2, Undo2 } from 'lucide-svelte';
	import { Alert, Badge, Button, Card } from '$lib/components/ui';
	import type { AppTemplate, ContainerResources, InstalledApp } from '$lib/api/apps';
	import { t } from '$lib/i18n';
	import AppResources from './AppResources.svelte';
	import AppStorageWarning from './AppStorageWarning.svelte';
	import { containerNames, HEALTH, stateOf, stoppedCompanions, webUrl } from './installed';

	type Props = {
		app: InstalledApp;
		/** Its app in the catalog, if the catalog has it: the icon, description and name. */
		template: AppTemplate | undefined;
		/** An install, update or rollback of it runs. */
		installing: boolean;
		onupdate: (app: InstalledApp) => void;
		onstart: (app: InstalledApp) => void;
		onstop: (app: InstalledApp) => void;
		onrestart: (app: InstalledApp) => void;
		onuninstall: (app: InstalledApp) => void;
		onrollback: (app: InstalledApp) => void;
		onremoveearlier: (app: InstalledApp) => void;
		onlimits: (target: { container: string; resources: ContainerResources }) => void;
	};

	let {
		app,
		template,
		installing,
		onupdate,
		onstart,
		onstop,
		onrestart,
		onuninstall,
		onrollback,
		onremoveearlier,
		onlimits
	}: Props = $props();

	// The group of the apps that are given share folders (ADR-0274): a name, the same in every language.
	const UNOS_SMB = 'unos-smb';

	const state = $derived(stateOf(app));
	const health = $derived(app.status === 'running' && app.health ? HEALTH[app.health] : undefined);
	const href = $derived(webUrl(app));
	// What Docker lacks (such an app does not start), else the companions that do not run.
	const warning = $derived.by(() => {
		if (app.missing.length > 0) return { key: 'apps.installed.missing', containers: app.missing };
		const stopped = stoppedCompanions(app);
		return stopped.length > 0 ? { key: 'apps.installed.companionStopped', containers: stopped } : null;
	});
</script>

<Card>
	<!-- On a narrow screen the buttons wrap below the app (ADR-0302). -->
	<div class="flex flex-wrap items-center gap-4">
		<div
			class="w-12 h-12 rounded-xl bg-surface-overlay flex items-center justify-center text-2xl flex-shrink-0"
		>
			{template?.icon || '📦'}
		</div>
		<div class="flex-1 min-w-0">
			<div class="flex flex-wrap items-center gap-2 mb-1">
				<h3 class="font-semibold text-lg break-all">{app.name}</h3>
				<Badge variant={state.variant}>{$t(state.key)}</Badge>
				{#if health}
					<Badge variant={health.variant}>{$t(health.key)}</Badge>
				{/if}
			</div>
			<p class="text-sm text-secondary truncate">
				{template?.description || app.template_id}
			</p>
			{#if app.web_port}
				<p class="text-xs text-muted mt-1">
					{$t('apps.installed.port')} <span class="mono">{app.web_port}</span>
				</p>
			{/if}
			{#if app.pool}
				<p class="text-xs text-muted mt-1">
					{$t('apps.installed.pool')} <span class="mono">{app.pool}</span>
				</p>
			{/if}
			{#if app.runs_in_unos_smb}
				<!-- Only such an app reads share folders, also after a Repair (ADR-0274). -->
				<p class="text-xs text-muted mt-1">
					{$t('apps.installed.inGroup')} <span class="mono">{UNOS_SMB}</span>
				</p>
			{/if}
			{#if app.companions.length > 0 || app.missing.length > 0}
				<!-- An app of several containers (ADR-0381): those that Docker has. -->
				<p class="text-xs text-muted mt-1">
					{$t('apps.installed.containers')}
					<span class="mono break-words">{containerNames(app).join(', ')}</span>
				</p>
			{/if}
		</div>
		<div class="flex flex-wrap items-center gap-2">
			{#if app.update_to && !app.previous && !installing}
				<!-- The catalog pins newer images: the dialog shows what the update does (ADR-0408). -->
				<Button variant="primary" size="sm" icon={ArrowUpCircle} onclick={() => onupdate(app)}>
					{$t('appUpdates.available', { version: app.update_to })}
				</Button>
			{/if}
			{#if href && app.missing.length === 0}
				<Button {href} target="_blank" rel="noopener" variant="secondary">
					<ExternalLink class="w-4 h-4" />
					{$t('apps.open')}
				</Button>
			{/if}
			<!-- An incomplete app does not start (409): a stop of what runs, and Uninstall. -->
			{#if app.status === 'running'}
				<Button
					variant="warning"
					size="sm"
					icon={Square}
					onclick={() => onstop(app)}
				>
					{$t('apps.installed.stop')}
				</Button>
				{#if app.missing.length === 0}
					<Button
						variant="secondary"
						size="sm"
						icon={RotateCw}
						onclick={() => onrestart(app)}
					>
						{$t('apps.installed.restart')}
					</Button>
				{/if}
			{:else if app.missing.length === 0}
				<Button
					variant="success"
					size="sm"
					icon={Play}
					onclick={() => onstart(app)}
				>
					{$t('apps.installed.start')}
				</Button>
			{/if}
			<Button
				variant="danger"
				size="sm"
				icon={Trash2}
				onclick={() => onuninstall(app)}
			>
				{$t('apps.installed.uninstall')}
			</Button>
		</div>
	</div>
	{#if warning}
		<Alert variant="warning" class="mt-4">
			<p>
				{$t(warning.key, {
					name: template?.name ?? app.name,
					containers: warning.containers.join(', ')
				})}
			</p>
		</Alert>
	{/if}
	{#if app.previous}
		{@const earlier = app.previous}
		<!-- The earlier version that the last update kept, and its copy of the database (ADR-0408). -->
		<Alert variant="info" title={$t('appUpdates.earlierTitle')} class="mt-4">
			<p class="break-words">
				{earlier.copies.length > 0
					? $t('appUpdates.earlier', {
							containers: earlier.containers.join(', '),
							copies: earlier.copies.join(', ')
						})
					: $t('appUpdates.earlierNoCopy', { containers: earlier.containers.join(', ') })}
			</p>
			{#if earlier.version}
				<p class="mt-1">{$t('appUpdates.earlierVersion', { version: earlier.version })}</p>
			{/if}
			<div class="flex flex-wrap gap-2 mt-3">
				<Button
					variant="warning"
					size="sm"
					icon={Undo2}
					disabled={installing}
					onclick={() => onrollback(app)}
				>
					{$t('appUpdates.rollback')}
				</Button>
				<Button
					variant="secondary"
					size="sm"
					icon={Trash2}
					disabled={installing}
					onclick={() => onremoveearlier(app)}
				>
					{$t('appUpdates.removeEarlier')}
				</Button>
			</div>
		</Alert>
	{/if}
	<AppStorageWarning {app} class="mt-4" />
	<AppResources {app} {onlimits} />
</Card>
