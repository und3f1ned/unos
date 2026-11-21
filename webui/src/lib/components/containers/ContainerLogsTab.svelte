<script lang="ts">
	// The Logs tab of a container's drawer: the state of the log's stream, Reconnect once it ended
	// or was lost, and the text, which follows new lines while it is scrolled to its end.
	import { RotateCw, ScrollText } from 'lucide-svelte';
	import { Alert, Button, EmptyState, StatusDot } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { ContainerLog, LogState } from './containerLog.svelte';

	type Props = {
		/** The log that the drawer streams. */
		log: ContainerLog;
		/** The container's name, which names the log for screen readers. */
		name: string;
	};

	let { log, name }: Props = $props();

	// What the Logs tab says in each state but idle: keys of the catalog.
	const LOG_STATE_KEYS: Record<Exclude<LogState, 'idle'>, string> = {
		connecting: 'containers.drawer.log.connecting',
		live: 'containers.drawer.log.live',
		ended: 'containers.drawer.log.ended',
		lost: 'containers.drawer.log.lost',
		forbidden: 'containers.drawer.log.forbidden'
	};
</script>

<div class="flex flex-col h-full gap-4">
	<div class="flex items-center gap-3">
		<p class="flex items-center gap-2 text-sm text-content-secondary" aria-live="polite">
			<StatusDot
				status={log.state === 'live'
					? 'success'
					: log.state === 'connecting'
						? 'warning'
						: log.state === 'idle'
							? 'neutral'
							: 'error'}
				pulse={log.state === 'live'}
			/>
			{log.state === 'idle' ? '' : $t(LOG_STATE_KEYS[log.state])}
		</p>
		{#if log.state === 'ended' || log.state === 'lost'}
			<Button variant="secondary" size="sm" icon={RotateCw} onclick={() => log.reconnect()}>
				{$t('containers.drawer.reconnect')}
			</Button>
		{/if}
	</div>

	{#if log.error}
		<Alert variant="error" title={$t('containers.drawer.logError')}>{log.error}</Alert>
	{/if}

	<div class="flex-1 min-h-0">
		{#if log.text}
			<pre
				bind:this={log.view}
				aria-label={$t('containers.drawer.logOf', { name })}
				class="bg-surface rounded-lg p-4 text-xs font-mono text-content-secondary overflow-auto h-full max-h-[60vh] scrollbar-thin whitespace-pre-wrap">{log.text}</pre>
		{:else if log.state === 'live'}
			<EmptyState compact icon={ScrollText} title={$t('containers.drawer.noLog')} />
		{/if}
	</div>
</div>
