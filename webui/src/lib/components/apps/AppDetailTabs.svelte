<script lang="ts">
	// The tabs of an app's details (Tabs, ADR-0432); a tab without content (no ports, folders or
	// variables) is not shown. The details draw the panel (`app-detail-panel-<tab>`).
	import { HardDrive, Network, Package, Settings } from 'lucide-svelte';
	import { Tabs } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { DetailTab } from './detail';

	type Props = {
		/** The app's name, which names the tabs for screen readers. */
		name: string;
		active: DetailTab;
		portCount: number;
		volumeCount: number;
		variableCount: number;
		onselect: (tab: DetailTab) => void;
	};

	let { name, active, portCount, volumeCount, variableCount, onselect }: Props = $props();

	const tabs = $derived([
		{ id: 'overview' as DetailTab, label: $t('apps.detail.tabs.overview'), icon: Package },
		...(portCount > 0
			? [{ id: 'ports' as DetailTab, label: $t('apps.detail.tabs.ports', { count: portCount }), icon: Network }]
			: []),
		...(volumeCount > 0
			? [{ id: 'volumes' as DetailTab, label: $t('apps.detail.tabs.volumes', { count: volumeCount }), icon: HardDrive }]
			: []),
		...(variableCount > 0
			? [
					{
						id: 'environment' as DetailTab,
						label: $t('apps.detail.tabs.environment', { count: variableCount }),
						icon: Settings
					}
				]
			: [])
	]);
</script>

<Tabs
	{tabs}
	{active}
	label={name}
	tabId={(id) => `app-detail-tab-${id}`}
	panelId={(id) => `app-detail-panel-${id}`}
	{onselect}
	class="mb-4"
/>
