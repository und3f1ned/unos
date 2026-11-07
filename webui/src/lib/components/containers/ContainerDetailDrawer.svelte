<script lang="ts">
	import { createEventDispatcher, onDestroy, tick } from 'svelte';
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

	export let container: Container | null = null;
	export let open = false;

	const dispatch = createEventDispatcher();

	// The log view keeps this much of the newest text, cut at a line break (ADR-0248).
	const MAX_LOG_CHARS = 500_000;
	// Closer to the end than this (pixels), the view follows new lines.
	const FOLLOW_SLACK_PX = 24;

	type LogState = 'idle' | 'connecting' | 'live' | 'ended' | 'lost' | 'forbidden';
	const LOG_STATE_TEXT: Record<LogState, string> = {
		idle: '',
		connecting: 'Connecting to the log...',
		live: 'Live: new lines appear as the container writes them',
		ended: 'The log stream ended',
		lost: 'The connection to the log was lost',
		forbidden: 'Only administrators can read container logs'
	};

	let activeTab: 'overview' | 'logs' | 'network' = 'overview';
	let logs = '';
	let logsError = '';
	let logState: LogState = 'idle';
	let logStream: ContainerLogStream | null = null;
	let streamedId: string | null = null;
	let logView: HTMLPreElement | undefined;
	let actionLoading = '';
	let copiedId = false;

	// The log streams while the Logs tab of an open drawer shows it, and only then.
	$: logsOf = open && activeTab === 'logs' && container ? container.id : null;
	$: followLogs(logsOf);

	onDestroy(() => {
		stopLogs();
	});

	function close() {
		dispatch('close');
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
			const cut = next.indexOf('\n', next.length - MAX_LOG_CHARS);
			next = cut === -1 ? next.slice(-MAX_LOG_CHARS) : next.slice(cut + 1);
		}
		logs = next;
		if (atEnd) {
			await tick();
			logView?.scrollTo({ top: logView.scrollHeight });
		}
	}

	async function handleAction(action: 'start' | 'stop' | 'restart' | 'remove') {
		if (!container) return;

		if (action === 'remove') {
			if (!confirm(`Are you sure you want to remove "${container.name}"? This cannot be undone.`))
				return;
		}

		actionLoading = action;
		try {
			switch (action) {
				case 'start':
					await startContainer(container.id);
					break;
				case 'stop':
					await stopContainer(container.id);
					break;
				case 'restart':
					await restartContainer(container.id);
					break;
				case 'remove':
					await removeContainer(container.id, true);
					close();
					break;
			}
			dispatch('action', { action, containerId: container.id });
		} catch (e: any) {
			alert(e.message || `${action} failed`);
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

	function formatDate(dateStr: string): string {
		return new Date(dateStr).toLocaleString();
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
	on:keydown={(e) => {
		if (e.key === 'Escape' && open) close();
	}}
/>

{#if open && container}
	<!-- Backdrop -->
	<div class="fixed inset-0 bg-black/50 z-40" on:click={close} role="presentation"></div>

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
				<Badge variant={getStateVariant(container.state)}>{container.state}</Badge>
				{#if getWebUrl(container)}
					<a
						href={getWebUrl(container)}
						target="_blank"
						rel="noopener"
						class="btn btn-secondary btn-icon"
						title="Open in browser"
					>
						<ExternalLink class="w-4 h-4" />
					</a>
				{/if}
				<button on:click={close} class="btn btn-ghost btn-icon">
					<X class="w-5 h-5" />
				</button>
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
					on:click={() => handleAction('stop')}
				>
					Stop
				</Button>
				<Button
					variant="secondary"
					size="sm"
					icon={RotateCw}
					loading={actionLoading === 'restart'}
					on:click={() => handleAction('restart')}
				>
					Restart
				</Button>
			{:else}
				<Button
					variant="success"
					size="sm"
					icon={Play}
					loading={actionLoading === 'start'}
					on:click={() => handleAction('start')}
				>
					Start
				</Button>
			{/if}
			<div class="flex-1"></div>
			<Button
				variant="danger"
				size="sm"
				icon={Trash2}
				loading={actionLoading === 'remove'}
				on:click={() => handleAction('remove')}
			>
				Remove
			</Button>
		</div>

		<!-- Tabs -->
		<div class="flex border-b border-border px-2">
			<button
				class="flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors {activeTab ===
				'overview'
					? 'text-info border-b-2 border-blue-500'
					: 'text-secondary hover:text-content'}"
				on:click={() => (activeTab = 'overview')}
			>
				<Info class="w-4 h-4" />
				Overview
			</button>
			<button
				class="flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors {activeTab ===
				'logs'
					? 'text-info border-b-2 border-blue-500'
					: 'text-secondary hover:text-content'}"
				on:click={() => (activeTab = 'logs')}
			>
				<Terminal class="w-4 h-4" />
				Logs
			</button>
			<button
				class="flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors {activeTab ===
				'network'
					? 'text-info border-b-2 border-blue-500'
					: 'text-secondary hover:text-content'}"
				on:click={() => (activeTab = 'network')}
			>
				<Network class="w-4 h-4" />
				Network
			</button>
		</div>

		<!-- Tab Content -->
		<div class="flex-1 overflow-y-auto p-5 scrollbar-thin">
			{#if activeTab === 'overview'}
				<div class="space-y-5">
					<!-- Container ID -->
					<div class="card-nested p-4">
						<div class="flex items-center justify-between mb-2">
							<span class="text-secondary text-sm font-medium">Container ID</span>
							<button
								on:click={copyId}
								class="p-1.5 rounded hover:bg-surface-overlay text-secondary hover:text-content transition-colors"
								title="Copy ID"
							>
								{#if copiedId}
									<Check class="w-4 h-4 text-success" />
								{:else}
									<Copy class="w-4 h-4" />
								{/if}
							</button>
						</div>
						<code class="mono-xs text-content break-all select-all">{container.id}</code>
					</div>

					<!-- Status & Created -->
					<div class="grid grid-cols-2 gap-4">
						<div class="card-nested p-4">
							<div class="text-secondary text-sm font-medium mb-2">Status</div>
							<div class="font-medium text-lg">{container.status}</div>
						</div>
						<div class="card-nested p-4">
							<div class="text-secondary text-sm font-medium mb-2">Created</div>
							<div class="font-medium">{formatDate(container.created)}</div>
						</div>
					</div>

					<!-- Image -->
					<div class="card-nested p-4">
						<div class="text-secondary text-sm font-medium mb-2">Image</div>
						<code class="mono text-content break-all">{container.image}</code>
					</div>

					<!-- Labels -->
					{#if Object.keys(container.labels || {}).length > 0}
						<div class="card-nested p-4">
							<div class="flex items-center gap-2 mb-4">
								<Tag class="w-4 h-4 text-secondary" />
								<span class="text-secondary text-sm font-medium">Labels</span>
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
							{LOG_STATE_TEXT[logState]}
						</p>
						{#if logState === 'ended' || logState === 'lost'}
							<Button variant="secondary" size="sm" icon={RotateCw} on:click={reconnectLogs}>
								Reconnect
							</Button>
						{/if}
					</div>

					{#if logsError}
						<Alert variant="error" title="The log could not be read">{logsError}</Alert>
					{/if}

					<div class="flex-1 min-h-0">
						{#if logs}
							<pre
								bind:this={logView}
								aria-label="Log of {container.name}"
								class="bg-surface rounded-lg p-4 text-xs font-mono text-content-secondary overflow-auto h-full max-h-[60vh] scrollbar-thin whitespace-pre-wrap">{logs}</pre>
						{:else if logState === 'live'}
							<p class="text-center text-content-muted py-8">The container has written no log yet</p>
						{/if}
					</div>
				</div>
			{:else if activeTab === 'network'}
				<div class="space-y-4">
					{#if container.ports.length > 0}
						<div class="space-y-3">
							<div class="flex items-center gap-2 mb-2">
								<Network class="w-4 h-4 text-secondary" />
								<span class="text-secondary text-sm font-medium">Port Mappings</span>
							</div>
							{#each container.ports as port}
								<div
									class="card-nested p-4 flex items-center justify-between"
								>
									<div class="flex items-center gap-4">
										<div class="text-center">
											<div class="text-xs text-secondary mb-1">Host</div>
											<div class="font-mono font-medium text-lg">
												{port.host_port || '-'}
											</div>
										</div>
										<div class="text-secondary">→</div>
										<div class="text-center">
											<div class="text-xs text-secondary mb-1">Container</div>
											<div class="font-mono font-medium text-lg">
												{port.container_port}
											</div>
										</div>
									</div>
									<Badge variant="info">{port.protocol.toUpperCase()}</Badge>
								</div>
							{/each}
						</div>
					{:else}
						<div class="text-center py-12">
							<Network class="w-12 h-12 mx-auto mb-3 text-muted opacity-50" />
							<p class="text-secondary font-medium">No port mappings</p>
							<p class="text-muted text-sm">This container has no exposed ports</p>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	</div>
{/if}
