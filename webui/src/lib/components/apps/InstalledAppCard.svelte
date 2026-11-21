<script lang="ts">
	// An installed app on the App Store's Installed tab: its state and health, its facts (port,
	// pool, group, containers) as labels over values, its buttons, what Docker lacks or does not
	// run, the earlier version that its last update kept (ADR-0408), its data outside the pools, and
	// its containers' resources. The buttons are quiet and in one place, the top right: Update,
	// Open and Start or Stop as secondary buttons, and Restart and Uninstall, last and in red, in its
	// ⋯ menu (ADR-0432, ADR-0437); no solid colours, which are for a confirming dialog's button.
	import { ArrowUpCircle, ExternalLink, PackageX, Play, RotateCw, Square, Trash2, Undo2 } from 'lucide-svelte';
	import { ActionMenu, Alert, Badge, Button, Card, type MenuItem } from '$lib/components/ui';
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
	// Its ⋯ menu: Restart while it runs and Docker has all of it, and Uninstall, which removes every
	// container of the app.
	const more = $derived<MenuItem[]>([
		...(app.status === 'running' && app.missing.length === 0
			? [{ label: $t('apps.installed.restart'), icon: RotateCw, onclick: () => onrestart(app) }]
			: []),
		{ label: $t('apps.installed.uninstall'), icon: PackageX, danger: true, onclick: () => onuninstall(app) }
	]);
	const several = $derived(app.companions.length > 0 || app.missing.length > 0);
	// What Docker lacks (such an app does not start), else the companions that do not run.
	const warning = $derived.by(() => {
		if (app.missing.length > 0) return { key: 'apps.installed.missing', containers: app.missing };
		const stopped = stoppedCompanions(app);
		return stopped.length > 0 ? { key: 'apps.installed.companionStopped', containers: stopped } : null;
	});
</script>

{#snippet fact(label: string, value: string, field: string)}
	<div class="flex flex-col gap-0.5 min-w-0">
		<span class="kv-label">{label}</span>
		<span class="kv-value mono text-[13px]" data-field={field}>{value}</span>
	</div>
{/snippet}

<Card>
	<div class="flex flex-wrap items-start gap-x-4 gap-y-3">
		<div
			class="w-11 h-11 rounded-xl bg-surface-overlay flex items-center justify-center text-2xl flex-shrink-0"
			aria-hidden="true"
		>
			{template?.icon || '📦'}
		</div>
		<div class="flex-1 min-w-[12rem]">
			<div class="flex flex-wrap items-center gap-2">
				<h3 class="font-semibold text-lg break-all">{app.name}</h3>
				<Badge variant={state.variant} dot>{$t(state.key)}</Badge>
				{#if health}
					<Badge variant={health.variant}>{$t(health.key)}</Badge>
				{/if}
			</div>
			<p class="text-sm text-content-secondary truncate" title={template?.description || app.template_id}>
				{template?.description || app.template_id}
			</p>
		</div>
		<!-- On a narrow screen the buttons wrap below the app (ADR-0302). -->
		<div class="flex flex-wrap items-center gap-2">
			{#if app.update_to && !app.previous && !installing}
				<!-- The catalog pins newer images: the dialog shows what the update does (ADR-0408). -->
				<Button variant="secondary" size="sm" icon={ArrowUpCircle} onclick={() => onupdate(app)}>
					{$t('appUpdates.available', { version: app.update_to })}
				</Button>
			{/if}
			{#if href && app.missing.length === 0}
				<Button {href} target="_blank" rel="noopener" variant="secondary" size="sm" icon={ExternalLink}>
					{$t('apps.open')}
				</Button>
			{/if}
			<!-- An incomplete app does not start (409): a stop of what runs, and Uninstall. -->
			{#if app.status === 'running'}
				<Button variant="secondary" size="sm" icon={Square} onclick={() => onstop(app)}>
					{$t('apps.installed.stop')}
				</Button>
			{:else if app.missing.length === 0}
				<Button variant="secondary" size="sm" icon={Play} onclick={() => onstart(app)}>
					{$t('apps.installed.start')}
				</Button>
			{/if}
			<ActionMenu items={more} name={app.name} />
		</div>
	</div>

	{#if app.web_port || app.pool || app.runs_in_unos_smb || several}
		<!-- Its facts, a label over each value. -->
		<div class="mt-3 flex flex-wrap gap-x-10 gap-y-3 sm:pl-[3.75rem]">
			{#if app.web_port}
				{@render fact($t('apps.installed.port'), String(app.web_port), 'port')}
			{/if}
			{#if app.pool}
				{@render fact($t('apps.installed.pool'), app.pool, 'pool')}
			{/if}
			{#if app.runs_in_unos_smb}
				<!-- Only such an app reads share folders, also after a Repair (ADR-0274). -->
				{@render fact($t('apps.installed.group'), UNOS_SMB, 'group')}
			{/if}
			{#if several}
				<!-- An app of several containers (ADR-0381): those that Docker has. -->
				{@render fact($t('apps.installed.containers'), containerNames(app).join(', '), 'containers')}
			{/if}
		</div>
	{/if}

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
					variant="secondary"
					size="sm"
					icon={Undo2}
					disabled={installing}
					disabledReason={installing ? $t('apps.installed.busy') : ''}
					onclick={() => onrollback(app)}
				>
					{$t('appUpdates.rollback')}
				</Button>
				<Button
					variant="ghost"
					size="sm"
					icon={Trash2}
					disabled={installing}
					disabledReason={installing ? $t('apps.installed.busy') : ''}
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
