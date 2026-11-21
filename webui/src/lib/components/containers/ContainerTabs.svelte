<script module lang="ts">
	export type TabId = 'containers' | 'images';
</script>

<script lang="ts">
	// The Containers / Images tabs of the Containers page (Tabs: the arrow keys, Home and End).
	import { Tabs } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		active: TabId;
		onselect: (tab: TabId) => void;
	};

	let { active, onselect }: Props = $props();
</script>

<Tabs
	tabs={[
		{ id: 'containers', label: $t('containers.tabs.containers') },
		{ id: 'images', label: $t('containers.tabs.images') }
	]}
	{active}
	label={$t('containers.tabs.label')}
	tabId={(id) => `docker-tab-${id}`}
	panelId={(id) => `docker-panel-${id}`}
	{onselect}
/>
