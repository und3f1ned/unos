<script lang="ts">
	import { onDestroy, tick, untrack } from 'svelte';
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
		Box,
		Copy,
		Check,
		Tag
	} from 'lucide-svelte';
	import { Alert, Button, Badge, StatusDot } from '$lib/components/ui';
	import {
		type Container,
		type ContainerLogStream,
		startContainer,
		stopContainer,
		restartContainer,
		removeContainer,
		streamContainerLogs
	} from '$lib/api/containers';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';

	type Action = 'start' | 'stop' | 'restart' | 'remove';

	type Props = {
		container?: Container | null;
		open?: boolean;
		/** An action on several containers runs (ADR-0389): the drawer's own actions wait for its end. */
		busy?: boolean;
		/** Escape, the backdrop, the close button or the container's removal asked to close it. */
		onclose?: () => void;
		/** An action on the container succeeded. */
		onaction?: (done: { action: Action; containerId: string }) => void;
	};

	let { container = null, open = $bindable(false), busy = false, onclose, onaction }: Props = $props();

	// The log view keeps this much of the newest text, cut at a line break (ADR-0255).
	const MAX_LOG_CHARS = 500_000;
	// Closer to the end than this (pixels), the view follows new lines.
	const FOLLOW_SLACK_PX = 24;

	type LogState = 'idle' | 'connecting' | 'live' | 'ended' | 'lost' | 'forbidden';
	// What the Logs tab says in each state but idle: keys of the catalog.
	const LOG_STATE_KEYS: Record<Exclude<LogState, 'idle'>, string> = {
		connecting: 'containers.drawer.log.connecting',
		live: 'containers.drawer.log.live',
		ended: 'containers.drawer.log.ended',
		lost: 'containers.drawer.log.lost',
		forbidden: 'containers.drawer.log.forbidden'
	};

	// What an action that failed without a message from the server says.
	const ACTION_FAILED_KEYS: Record<Action, string> = {
		start: 'containers.drawer.startFailed',
		stop: 'containers.drawer.stopFailed',
		restart: 'containers.drawer.restartFailed',
		remove: 'containers.drawer.removeFailed'
	};

	// Docker's words that the server passes on: keys of the catalog, by value. A container's
	// state and status (State.Status; the Containers page, src/routes/containers/+page.svelte,
	// has the same map) and a port's protocol.
	const STATES: Record<string, string> = {
		created: 'containers.state.created',
		running: 'containers.state.running',
		paused: 'containers.state.paused',
		restarting: 'containers.state.restarting',
		removing: 'containers.state.removing',
		exited: 'containers.state.exited',
		dead: 'containers.state.dead'
	};
	const PROTOCOLS: Record<string, string> = {
		tcp: 'containers.protocol.tcp',
		udp: 'containers.protocol.udp',
		sctp: 'containers.protocol.sctp'
	};

	/** The label of `value` in the language of the page; a value without one as it comes. */
	function labelOf(labels: Record<string, string>, value: string): string {
		return Object.prototype.hasOwnProperty.call(labels, value) ? $t(labels[value]) : value;
	}

	let activeTab = $state<'overview' | 'logs' | 'network'>('overview');
	let logs = $state('');
	let logsError = $state('');
	let logState = $state<LogState>('idle');
	let logStream: ContainerLogStream | null = null;
	let streamedId: string | null = null;
	let logView = $state<HTMLPreElement>();
	let actionLoading = $state('');
	let copiedId = $state(false);

	// The log streams while the Logs tab of an open drawer shows it, and only then.
	const logsOf = $derived(open && activeTab === 'logs' && container ? container.id : null);
	// Before the markup updates, as Svelte 4's statement did: the Logs tab first shows the new
	// stream's state, not the last one's text. Only `logsOf` makes it run again.
	$effect.pre(() => {
		const id = logsOf;
		untrack(() => followLogs(id));
	});

	onDestroy(() => {
		stopLogs();
	});

	function close() {
		onclose?.();
	}

	function followLogs(id: string | null) {
		if (id === streamedId) return;
		stopLogs();
		if (id !== null) startLogs(id);
	}

	function startLogs(id: string) {
		streamedId = id;
		logs = '';
		logsError = '';
		logState = 'connecting';
		logStream = streamContainerLogs(id, {
			onLive: () => (logState = 'live'),
			onLog: appendLog,
			onError: (message) => (logsError = message),
			onEnd: (reason) => {
				logStream = null;
				logState = reason;
			}
		});
	}

	function stopLogs() {
		logStream?.close();
		logStream = null;
		streamedId = null;
		logState = 'idle';
	}

	function reconnectLogs() {
		const id = streamedId;
		stopLogs();
		if (id !== null) startLogs(id);
	}

	async function appendLog(data: string) {
		const atEnd =
			!logView || logView.scrollHeight - logView.scrollTop - logView.clientHeight < FOLLOW_SLACK_PX;
		let next = logs + data;
		if (next.length > MAX_LOG_CHARS) {
			// Search from the last character that must go: the first line break from there
			// ends the last dropped line, so what follows is the most whole lines that fit.
			const cut = next.indexOf('\n', next.length - MAX_LOG_CHARS - 1);
			next = cut === -1 ? next.slice(-MAX_LOG_CHARS) : next.slice(cut + 1);
		}
		logs = next;
		if (atEnd) {
			await tick();
			logView?.scrollTo({ top: logView.scrollHeight });
		}
	}

	async function handleAction(action: Action) {
		// The container the action is for: a prop reads the page's value at once, and close()
		// makes the page drop it before the action is reported.
		const target = container;
		if (!target) return;

		if (action === 'remove') {
			if (!confirm($t('containers.drawer.confirmRemove', { name: target.name }))) return;
		}

		actionLoading = action;
		try {
			switch (action) {
				case 'start':
					await startContainer(target.id);
					break;
				case 'stop':
					await stopContainer(target.id);
					break;
				case 'restart':
					await restartContainer(target.id);
					break;
				case 'remove':
					await removeContainer(target.id, true);
					close();
					break;
			}
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

	function getStateVariant(
		state: string
	): 'success' | 'error' | 'warning' | 'info' | 'neutral' | 'purple' | 'cyan' | 'orange' {
		switch (state) {
			case 'running':
				return 'success';
			case 'exited':
				return 'error';
			case 'paused':
				return 'warning';
			case 'restarting':
				return 'info';
			default:
				return 'neutral';
		}
	}

	// It reads $intlLocale, so that an open drawer follows a change of the language.
	function formatDate(dateStr: string): string {
		return formatDateTime(dateStr, $intlLocale);
	}

	function getWebUrl(container: Container): string | null {
		const webPort = container.ports.find((p) => p.host_port);
		if (webPort) {
			return `http://${window.location.hostname}:${webPort.host_port}`;
		}
		return null;
	}
</script>

<svelte:window
	onkeydown={(e) => {
		if (e.key === 'Escape' && open) close();
	}}
/>

{#if open && container}
	<!-- Backdrop -->
	<div class="fixed inset-0 scrim z-40" onclick={close} role="presentation"></div>

	<!-- Drawer -->
	<div
		class="fixed right-0 top-0 h-full w-full max-w-2xl bg-surface-elevated z-50 shadow-2xl flex flex-col"
		role="dialog"
		aria-modal="true"
		aria-labelledby="container-drawer-title"
	>
		<!-- Header -->
		<div class="flex items-center justify-between p-5 border-b border-border">
			<div class="flex items-center gap-4 min-w-0">
				<div
					class="w-12 h-12 rounded-xl bg-surface-overlay flex items-center justify-center flex-shrink-0"
				>
					<Box class="w-6 h-6 icon-container" />
				</div>
				<div class="min-w-0">
					<h2 id="container-drawer-title" class="font-semibold text-xl truncate">
						{container.name}
					</h2>
					<p class="text-sm text-secondary truncate">{container.image}</p>
				</div>
			</div>
			<div class="flex items-center gap-3">
				<Badge variant={getStateVariant(container.state)}>{labelOf(STATES, container.state)}</Badge>
				{#if getWebUrl(container)}
					<Button
						href={getWebUrl(container)}
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
			<Button variant="plain"
			 class="tab-underline py-3 {activeTab === 'overview' ? 'tab-underline-active' : ''}"
				onclick={() => (activeTab = 'overview')}
			>
				<Info class="w-4 h-4" />
				{$t('containers.drawer.overview')}
			</Button>
			<Button variant="plain"
			 class="tab-underline py-3 {activeTab === 'logs' ? 'tab-underline-active' : ''}"
				onclick={() => (activeTab = 'logs')}
			>
				<Terminal class="w-4 h-4" />
				{$t('containers.drawer.logs')}
			</Button>
			<Button variant="plain"
			 class="tab-underline py-3 {activeTab === 'network' ? 'tab-underline-active' : ''}"
				onclick={() => (activeTab = 'network')}
			>
				<Network class="w-4 h-4" />
				{$t('containers.drawer.network')}
			</Button>
		</div>

		<!-- Tab Content -->
		<div class="flex-1 overflow-y-auto p-5 scrollbar-thin">
			{#if activeTab === 'overview'}
				<div class="space-y-5">
					<!-- Container ID -->
					<div class="card-nested p-4">
						<div class="flex items-center justify-between mb-2">
							<span class="text-secondary text-sm font-medium">{$t('containers.drawer.id')}</span>
							<Button variant="ghost" size="icon-sm" class="text-secondary"
								onclick={copyId}
								title={$t('containers.drawer.copyId')}
							>
								{#if copiedId}
									<Check class="w-4 h-4 text-success" />
								{:else}
									<Copy class="w-4 h-4" />
								{/if}
							</Button>
						</div>
						<code class="mono-xs text-content break-all select-all">{container.id}</code>
					</div>

					<!-- Status & Created -->
					<div class="grid grid-cols-2 gap-4">
						<div class="card-nested p-4">
							<div class="text-secondary text-sm font-medium mb-2">{$t('containers.drawer.status')}</div>
							<div class="font-medium text-lg">{labelOf(STATES, container.status)}</div>
						</div>
						<div class="card-nested p-4">
							<div class="text-secondary text-sm font-medium mb-2">{$t('containers.drawer.created')}</div>
							<div class="font-medium">{formatDate(container.created)}</div>
						</div>
					</div>

					<!-- Image -->
					<div class="card-nested p-4">
						<div class="text-secondary text-sm font-medium mb-2">{$t('containers.drawer.image')}</div>
						<code class="mono text-content break-all">{container.image}</code>
					</div>

					<!-- Labels -->
					{#if Object.keys(container.labels || {}).length > 0}
						<div class="card-nested p-4">
							<div class="flex items-center gap-2 mb-4">
								<Tag class="w-4 h-4 text-secondary" />
								<span class="text-secondary text-sm font-medium">{$t('containers.drawer.labels')}</span>
								<Badge variant="neutral">{Object.keys(container.labels).length}</Badge>
							</div>
							<div class="space-y-2 max-h-64 overflow-y-auto scrollbar-thin">
								{#each Object.entries(container.labels) as [key, value]}
									<div class="bg-surface-overlay rounded-lg p-3">
										<div class="text-xs text-secondary font-mono mb-1 break-all">{key}</div>
										<div class="text-sm text-content font-mono break-all">{value}</div>
									</div>
								{/each}
							</div>
						</div>
					{/if}
				</div>
			{:else if activeTab === 'logs'}
				<div class="flex flex-col h-full gap-4">
					<div class="flex items-center gap-3">
						<p class="flex items-center gap-2 text-sm text-content-secondary" aria-live="polite">
							<StatusDot
								status={logState === 'live'
									? 'success'
									: logState === 'connecting'
										? 'warning'
										: logState === 'idle'
											? 'neutral'
											: 'error'}
								pulse={logState === 'live'}
							/>
							{logState === 'idle' ? '' : $t(LOG_STATE_KEYS[logState])}
						</p>
						{#if logState === 'ended' || logState === 'lost'}
							<Button variant="secondary" size="sm" icon={RotateCw} onclick={reconnectLogs}>
								{$t('containers.drawer.reconnect')}
							</Button>
						{/if}
					</div>

					{#if logsError}
						<Alert variant="error" title={$t('containers.drawer.logError')}>{logsError}</Alert>
					{/if}

					<div class="flex-1 min-h-0">
						{#if logs}
							<pre
								bind:this={logView}
								aria-label={$t('containers.drawer.logOf', { name: container.name })}
								class="bg-surface rounded-lg p-4 text-xs font-mono text-content-secondary overflow-auto h-full max-h-[60vh] scrollbar-thin whitespace-pre-wrap">{logs}</pre>
						{:else if logState === 'live'}
							<p class="text-center text-content-muted py-8">
								{$t('containers.drawer.noLog')}
							</p>
						{/if}
					</div>
				</div>
			{:else if activeTab === 'network'}
				<div class="space-y-4">
					{#if container.ports.length > 0}
						<div class="space-y-3">
							<div class="flex items-center gap-2 mb-2">
								<Network class="w-4 h-4 text-secondary" />
								<span class="text-secondary text-sm font-medium">{$t('containers.drawer.ports')}</span>
							</div>
							{#each container.ports as port}
								<div
									class="card-nested p-4 flex items-center justify-between"
								>
									<div class="flex items-center gap-4">
										<div class="text-center">
											<div class="text-xs text-secondary mb-1">{$t('containers.drawer.host')}</div>
											<div class="font-mono font-medium text-lg">
												{port.host_port || '-'}
											</div>
										</div>
										<div class="text-secondary">→</div>
										<div class="text-center">
											<div class="text-xs text-secondary mb-1">{$t('containers.drawer.container')}</div>
											<div class="font-mono font-medium text-lg">
												{port.container_port}
											</div>
										</div>
									</div>
									<Badge variant="info">{labelOf(PROTOCOLS, port.protocol)}</Badge>
								</div>
							{/each}
						</div>
					{:else}
						<div class="text-center py-12">
							<Network class="w-12 h-12 mx-auto mb-3 text-muted opacity-50" />
							<p class="text-secondary font-medium">{$t('containers.drawer.noPorts')}</p>
							<p class="text-muted text-sm">{$t('containers.drawer.noPortsHint')}</p>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	</div>
{/if}
