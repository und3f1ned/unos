<script lang="ts">
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';
	import PageTabs from '$lib/components/layout/PageTabs.svelte';
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

	type Props = {
		children?: Snippet;
	};

	let { children }: Props = $props();

	// The System page's tabs (ADR-0428, ADR-0431), the NAS's and an administrator's only, in three
	// groups: Settings, what the NAS is set to; Monitoring, what it does; Maintenance, what is done
	// to it. The command palette lists them in this order (palette/choices.ts). Each tab is a page of
	// its own with its load (ADR-0470): General is /system, the others /system/<id>, so that a switch
	// of tabs keeps the open tab until the next one has its data. `form`: a tab of forms, which keeps
	// to a readable width. Reactive: the labels follow the language of the page.
	let tabs = $derived(
		[
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
		].map((tab) => ({ ...tab, href: tab.id === 'general' ? '/system' : `/system/${tab.id}` }))
	);

	const GROUPS = [
		{ id: 'settings', key: 'system.group.settings' },
		{ id: 'monitoring', key: 'system.group.monitoring' },
		{ id: 'maintenance', key: 'system.group.maintenance' }
	];
	let groups = $derived(
		GROUPS.map((group) => ({ id: group.id, label: $t(group.key), tabs: tabs.filter((tab) => tab.group === group.id) }))
	);

	// The open tab is the page's: SvelteKit changes the path once the next tab has its data.
	let active = $derived(tabs.find((tab) => tab.href === page.url.pathname) ?? tabs[0]);
</script>

<!-- The page's h1, then from xl the tabs' groups in a 220 px column beside the tab; below xl
     three labelled rows of chips above it, so that the tab keeps the width (ADR-0428, PageTabs).
     The page scrolls as a whole. -->
<div class="page-container">
	<div class="page-header">
		<h1 id="system-title" class="page-title">{$t('system.heading.admin')}</h1>
	</div>

	<div class="grid gap-6 items-start xl:grid-cols-[220px_minmax(0,1fr)]">
		<PageTabs labelledby="system-title" idPrefix="system" {groups} active={active.id} />

		<!-- The open tab; a tab of forms keeps to a readable width. -->
		<div class="min-w-0 {active.form ? 'content-narrow' : ''}">
			{@render children?.()}
		</div>
	</div>
</div>
