<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import { Terminal, Info, Network, Box } from 'lucide-svelte';
	import { Badge, Sheet, Tabs } from '$lib/components/ui';
	import type { Container } from '$lib/api/containers';
	import { t } from '$lib/i18n';
	import { ContainerLog } from './containerLog.svelte';
	import { labelOf, stateVariant, STATES } from './labels';
	import ContainerLogsTab from './ContainerLogsTab.svelte';
	import ContainerNetworkTab from './ContainerNetworkTab.svelte';
	import ContainerOverviewTab from './ContainerOverviewTab.svelte';

	// The details of a container in a Sheet: its overview, log and ports, each a tab. Starting, stopping, removing and
	// opening it in the browser are its row's in the list, not a second copy here (ADR-0431).
	type Props = {
		container?: Container | null;
		open?: boolean;
		/** Escape, the backdrop or the Sheet's close button asked to close it. */
		onclose?: () => void;
	};

	let { container = null, open = $bindable(false), onclose }: Props = $props();

	type Tab = 'overview' | 'logs' | 'network';

	let activeTab = $state<Tab>('overview');
	const log = new ContainerLog();
	let copiedId = $state(false);

	// The log streams while the Logs tab of an open drawer shows it, and only then.
	const logsOf = $derived(open && activeTab === 'logs' && container ? container.id : null);
	// Before the markup updates, as Svelte 4's statement did: the Logs tab first shows the new
	// stream's state, not the last one's text. Only `logsOf` makes it run again.
	$effect.pre(() => {
		const id = logsOf;
		untrack(() => log.follow(id));
	});

	onDestroy(() => {
		log.stop();
	});

	function close() {
		onclose?.();
	}

	function copyId() {
		if (container) {
			navigator.clipboard.writeText(container.id);
			copiedId = true;
			setTimeout(() => (copiedId = false), 2000);
		}
	}
</script>

<!-- A Sheet (ADR-0432): its title the name, which wraps, the state once beside it (webui.ux-apps). -->
<Sheet open={open && container !== null} title={container?.name ?? ''} onclose={close}>
	{#snippet titleIcon()}
		<span
			class="w-9 h-9 rounded-card bg-surface-overlay flex items-center justify-center flex-shrink-0"
			aria-hidden="true"
		>
			<Box class="w-5 h-5 text-content-secondary" />
		</span>
	{/snippet}
	{#snippet status()}
		{#if container}
			<Badge variant={stateVariant(container.state)} dot>{labelOf(STATES, container.state, $t)}</Badge>
		{/if}
	{/snippet}
	{#if container}
		<Tabs
			tabs={[
				{ id: 'overview', label: $t('containers.drawer.overview'), icon: Info },
				{ id: 'logs', label: $t('containers.drawer.logs'), icon: Terminal },
				{ id: 'network', label: $t('containers.drawer.ports'), icon: Network }
			]}
			active={activeTab}
			label={container.name}
			tabId={(id) => `container-tab-${id}`}
			panelId={(id) => `container-panel-${id}`}
			onselect={(id) => (activeTab = id)}
			class="mb-5"
		/>
		<div role="tabpanel" id="container-panel-{activeTab}" aria-labelledby="container-tab-{activeTab}">
			{#if activeTab === 'overview'}
				<ContainerOverviewTab {container} copied={copiedId} oncopy={copyId} />
			{:else if activeTab === 'logs'}
				<ContainerLogsTab {log} name={container.name} />
			{:else if activeTab === 'network'}
				<ContainerNetworkTab ports={container.ports} />
			{/if}
		</div>
	{/if}
</Sheet>
