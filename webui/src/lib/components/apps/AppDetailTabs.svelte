<script lang="ts">
	// The tabs of an app's details; a tab without content (no ports, folders or variables) is not
	// shown. They wrap on a narrow screen (ADR-0302).
	import { HardDrive, Network, Package, Settings } from 'lucide-svelte';
	import { Button, type IconComponent } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { DetailTab } from './detail';

	type Props = {
		active: DetailTab;
		portCount: number;
		volumeCount: number;
		variableCount: number;
		onselect: (tab: DetailTab) => void;
	};

	let { active, portCount, volumeCount, variableCount, onselect }: Props = $props();
</script>

{#snippet tab(id: DetailTab, Icon: IconComponent, label: string)}
	<Button variant="plain"
	 class="tab-underline {active === id ? 'tab-underline-active' : ''}"
		onclick={() => onselect(id)}
	>
		<Icon class="w-4 h-4" />
		{label}
	</Button>
{/snippet}

<!-- Tabs: they wrap on a narrow screen (ADR-0302) -->
<div class="flex flex-wrap border-b border-border -mx-6 px-6 mb-4">
	{@render tab('overview', Package, $t('apps.detail.tabs.overview'))}
	{#if portCount > 0}
		{@render tab('ports', Network, $t('apps.detail.tabs.ports', { count: portCount }))}
	{/if}
	{#if volumeCount > 0}
		{@render tab('volumes', HardDrive, $t('apps.detail.tabs.volumes', { count: volumeCount }))}
	{/if}
	{#if variableCount > 0}
		{@render tab('environment', Settings, $t('apps.detail.tabs.environment', { count: variableCount }))}
	{/if}
</div>
