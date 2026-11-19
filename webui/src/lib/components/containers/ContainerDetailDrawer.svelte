<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import { on } from 'svelte/events';
	import {
		X,
		Play,
		Square,
		RotateCw,
		Trash2,
		Terminal,
		Info,
		Network,
		ExternalLink,
		Box
	} from 'lucide-svelte';
	import { Button, Badge, type IconComponent } from '$lib/components/ui';
	import type { Container } from '$lib/api/containers';
	import { t } from '$lib/i18n';
	import { containerAction } from './actions';
	import { ContainerLog } from './containerLog.svelte';
	import { labelOf, stateVariant, STATES, webUrl, type QuickAction } from './labels';
	import ContainerLogsTab from './ContainerLogsTab.svelte';
	import ContainerNetworkTab from './ContainerNetworkTab.svelte';
	import ContainerOverviewTab from './ContainerOverviewTab.svelte';

	type Props = {
		container?: Container | null;
		open?: boolean;
		/** An action on several containers runs (ADR-0389): the drawer's own actions wait for its end. */
		busy?: boolean;
		/** Escape, the backdrop, the close button or the container's removal asked to close it. */
		onclose?: () => void;
		/** An action on the container succeeded. */
		onaction?: (done: { action: QuickAction; containerId: string }) => void;
	};

	let { container = null, open = $bindable(false), busy = false, onclose, onaction }: Props = $props();

	// What an action that failed without a message from the server says.
	const ACTION_FAILED_KEYS: Record<QuickAction, string> = {
		start: 'containers.drawer.startFailed',
		stop: 'containers.drawer.stopFailed',
		restart: 'containers.drawer.restartFailed',
		remove: 'containers.drawer.removeFailed'
	};

	type Tab = 'overview' | 'logs' | 'network';

	let activeTab = $state<Tab>('overview');
	const log = new ContainerLog();
	let actionLoading = $state('');
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

	async function handleAction(action: QuickAction) {
		// The container the action is for: a prop reads the page's value at once, and close()
		// makes the page drop it before the action is reported.
		const target = container;
		if (!target) return;

		if (action === 'remove') {
			if (!confirm($t('containers.drawer.confirmRemove', { name: target.name }))) return;
		}

		actionLoading = action;
		try {
			await containerAction(target.id, action);
			if (action === 'remove') close();
			onaction?.({ action, containerId: target.id });
		} catch (e: any) {
			alert(e.message || $t(ACTION_FAILED_KEYS[action]));
		} finally {
			actionLoading = '';
		}
	}

	function copyId() {
		if (container) {
			navigator.clipboard.writeText(container.id);
			copiedId = true;
			setTimeout(() => (copiedId = false), 2000);
		}
	}
</script>

{#snippet tab(id: Tab, Icon: IconComponent, label: string)}
	<Button variant="plain"
	 class="tab-underline py-3 {activeTab === id ? 'tab-underline-active' : ''}"
		onclick={() => (activeTab = id)}
	>
		<Icon class="w-4 h-4" />
		{label}
	</Button>
{/snippet}

<svelte:window
	onkeydown={(e) => {
		if (e.key === 'Escape' && open) close();
	}}
/>

{#if open && container}
	{@const href = webUrl(container)}
	<!-- Backdrop: it listens for the click itself, not through Svelte's delegated listener at the
	     app's root, as Modal's overlay does (iOS Safari, and a click that does not bubble). -->
	<div class="fixed inset-0 scrim z-40" {@attach (node) => on(node, 'click', close)} role="presentation"></div>

	<!-- Drawer -->
	<div
		class="fixed right-0 top-0 h-full w-full md:max-w-[480px] bg-surface-elevated md:border-l border-border z-50 shadow-dialog flex flex-col"
		role="dialog"
		aria-modal="true"
		aria-labelledby="container-drawer-title"
	>
		<!-- Header -->
		<div class="flex items-center justify-between p-5 border-b border-border">
			<div class="flex items-center gap-4 min-w-0">
				<div
					class="w-10 h-10 rounded-card bg-surface-overlay flex items-center justify-center flex-shrink-0"
					aria-hidden="true"
				>
					<Box class="w-5 h-5 text-content-secondary" />
				</div>
				<div class="min-w-0">
					<h2 id="container-drawer-title" class="font-semibold text-lg truncate">
						{container.name}
					</h2>
					<p class="text-sm text-secondary truncate">{container.image}</p>
				</div>
			</div>
			<div class="flex items-center gap-3">
				<Badge variant={stateVariant(container.state)}>{labelOf(STATES, container.state, $t)}</Badge>
				{#if href}
					<Button
						{href}
						target="_blank"
						rel="noopener"
						variant="secondary"
						size="icon"
						title={$t('containers.openInBrowser')}
					>
						<ExternalLink class="w-4 h-4" />
					</Button>
				{/if}
				<Button variant="ghost" size="icon" onclick={close}>
					<X class="w-5 h-5" />
				</Button>
			</div>
		</div>

		<!-- Quick Actions -->
		<div class="flex items-center gap-3 p-5 border-b border-border bg-surface-muted">
			{#if container.state === 'running'}
				<Button
					variant="warning"
					size="sm"
					icon={Square}
					loading={actionLoading === 'stop'}
					disabled={busy}
					onclick={() => handleAction('stop')}
				>
					{$t('containers.action.stop')}
				</Button>
				<Button
					variant="secondary"
					size="sm"
					icon={RotateCw}
					loading={actionLoading === 'restart'}
					disabled={busy}
					onclick={() => handleAction('restart')}
				>
					{$t('containers.action.restart')}
				</Button>
			{:else}
				<Button
					variant="success"
					size="sm"
					icon={Play}
					loading={actionLoading === 'start'}
					disabled={busy}
					onclick={() => handleAction('start')}
				>
					{$t('containers.action.start')}
				</Button>
			{/if}
			<div class="flex-1"></div>
			<Button
				variant="danger"
				size="sm"
				icon={Trash2}
				loading={actionLoading === 'remove'}
				disabled={busy}
				onclick={() => handleAction('remove')}
			>
				{$t('containers.action.remove')}
			</Button>
		</div>

		<!-- Tabs -->
		<div class="flex border-b border-border px-2">
			{@render tab('overview', Info, $t('containers.drawer.overview'))}
			{@render tab('logs', Terminal, $t('containers.drawer.logs'))}
			{@render tab('network', Network, $t('containers.drawer.network'))}
		</div>

		<!-- Tab Content -->
		<div class="flex-1 overflow-y-auto p-5 scrollbar-thin">
			{#if activeTab === 'overview'}
				<ContainerOverviewTab {container} copied={copiedId} oncopy={copyId} />
			{:else if activeTab === 'logs'}
				<ContainerLogsTab {log} name={container.name} />
			{:else if activeTab === 'network'}
				<ContainerNetworkTab ports={container.ports} />
			{/if}
		</div>
	</div>
{/if}
