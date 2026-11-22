<script lang="ts">
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import PageTabs from '$lib/components/layout/PageTabs.svelte';
	import { tabFromHash } from '$lib/components/layout/tabs';
	import { t } from '$lib/i18n';
	import {
		Settings,
		Gauge,
		FileText,
		Heart,
		Database,
		Power,
		Calendar,
		Bell,
		Network,
		Shield,
		Globe,
		ArrowUpCircle
	} from 'lucide-svelte';

	// The System page's tabs (ADR-0428, ADR-0431), the NAS's and an administrator's only, in three
	// groups: Settings, what the NAS is set to; Monitoring, what it does; Maintenance, what is done
	// to it. The command palette lists them in this order (palette/choices.ts). `form`: a tab of
	// forms, which keeps to a readable width. Reactive: the labels follow the language of the page.
	let tabs = $derived([
		{ id: 'general', group: 'settings', icon: Settings, label: $t('system.tabs.general'), form: true },
		{ id: 'network', group: 'settings', icon: Network, label: $t('system.tabs.network'), form: false },
		{ id: 'remote', group: 'settings', icon: Globe, label: $t('remote.tab'), form: true },
		{ id: 'security', group: 'settings', icon: Shield, label: $t('system.tabs.security'), form: true },
		{ id: 'notifications', group: 'settings', icon: Bell, label: $t('system.tabs.notifications'), form: true },
		{ id: 'monitor', group: 'monitoring', icon: Gauge, label: $t('system.tabs.monitor'), form: false },
		{ id: 'logs', group: 'monitoring', icon: FileText, label: $t('system.tabs.logs'), form: false },
		{ id: 'health', group: 'monitoring', icon: Heart, label: $t('system.tabs.health'), form: false },
		{ id: 'updates', group: 'maintenance', icon: ArrowUpCircle, label: $t('system.tabs.updates'), form: true },
		{ id: 'backup', group: 'maintenance', icon: Database, label: $t('system.tabs.backup'), form: true },
		{ id: 'scheduler', group: 'maintenance', icon: Calendar, label: $t('system.tabs.scheduler'), form: false },
		{ id: 'power', group: 'maintenance', icon: Power, label: $t('system.tabs.power'), form: true }
	]);

	const GROUPS = [
		{ id: 'settings', key: 'system.group.settings' },
		{ id: 'monitoring', key: 'system.group.monitoring' },
		{ id: 'maintenance', key: 'system.group.maintenance' }
	];
	let groups = $derived(
		GROUPS.map((group) => ({ id: group.id, label: $t(group.key), tabs: tabs.filter((tab) => tab.group === group.id) }))
	);

	let activeTab = $state('general');

	// Follow the URL hash (links such as /system#notifications, reloads, bookmarks) before the page
	// is drawn, also the first time. It depends only on the URL and the tabs: the tab is set
	// untracked. If it depended on activeTab, every tab click would re-run it and switch straight
	// back to the tab named in the URL.
	$effect.pre(() => {
		const hash = page.url.hash;
		const available = tabs;
		untrack(() => (activeTab = tabFromHash(hash, available, activeTab)));
	});

	function setTab(id: string) {
		activeTab = id;
		// SvelteKit's goto, not history.replaceState: it updates page.url and
		// keeps the router's state in the history entry, which Back/Forward need.
		goto(`#${id}`, { replace: true, reset: false });
	}
</script>

<!-- The page's h1, then from xl the tabs' groups in a 220 px column beside the tab; below xl
     three labelled rows of chips above it, so that the tab keeps the width (ADR-0428, PageTabs).
     The page scrolls as a whole. -->
<div class="page-container">
	<div class="page-header">
		<h1 id="system-title" class="page-title">{$t('system.heading.admin')}</h1>
	</div>

	<div class="grid gap-6 items-start xl:grid-cols-[220px_minmax(0,1fr)]">
		<PageTabs labelledby="system-title" idPrefix="system" {groups} active={activeTab} onselect={setTab} />

		<!-- The open tab; a tab of forms keeps to a readable width. -->
		<div class="min-w-0 {tabs.find((tab) => tab.id === activeTab)?.form ? 'content-narrow' : ''}">
			{#if activeTab === 'general'}
				{#await import('./tabs/GeneralTab.svelte') then { default: GeneralTab }}
					<GeneralTab />
				{/await}
			{:else if activeTab === 'updates'}
				{#await import('./tabs/UpdateTab.svelte') then { default: UpdateTab }}
					<UpdateTab />
				{/await}
			{:else if activeTab === 'security'}
				{#await import('./tabs/SecurityTab.svelte') then { default: SecurityTab }}
					<SecurityTab />
				{/await}
			{:else if activeTab === 'monitor'}
				{#await import('./tabs/MonitorTab.svelte') then { default: MonitorTab }}
					<MonitorTab />
				{/await}
			{:else if activeTab === 'logs'}
				{#await import('./tabs/LogsTab.svelte') then { default: LogsTab }}
					<LogsTab />
				{/await}
			{:else if activeTab === 'health'}
				{#await import('./tabs/HealthTab.svelte') then { default: HealthTab }}
					<HealthTab />
				{/await}
			{:else if activeTab === 'backup'}
				{#await import('./tabs/BackupTab.svelte') then { default: BackupTab }}
					<BackupTab />
				{/await}
			{:else if activeTab === 'scheduler'}
				{#await import('./tabs/SchedulerTab.svelte') then { default: SchedulerTab }}
					<SchedulerTab />
				{/await}
			{:else if activeTab === 'notifications'}
				{#await import('./tabs/NotificationsTab.svelte') then { default: NotificationsTab }}
					<NotificationsTab />
				{/await}
			{:else if activeTab === 'network'}
				{#await import('./tabs/NetworkTab.svelte') then { default: NetworkTab }}
					<NetworkTab />
				{/await}
			{:else if activeTab === 'remote'}
				{#await import('./tabs/RemoteAccessTab.svelte') then { default: RemoteAccessTab }}
					<RemoteAccessTab />
				{/await}
			{:else if activeTab === 'power'}
				{#await import('./tabs/PowerTab.svelte') then { default: PowerTab }}
					<PowerTab />
				{/await}
			{/if}
		</div>
	</div>
</div>
