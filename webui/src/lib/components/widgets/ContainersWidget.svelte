<script lang="ts">
	import {
		Container,
		Play,
		Square,
		AlertCircle,
		CheckCircle2,
		XCircle,
		ExternalLink
	} from 'lucide-svelte';
	import { Card, StatusDot, Badge } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		containers?: Array<{
			id: string;
			name: string;
			state: string;
			image: string;
			ports?: Array<{ host_port: number | null; container_port: number; protocol: string }>;
		}>;
		compact?: boolean;
		limit?: number;
	};

	let { containers = [], compact = false, limit = 5 }: Props = $props();

	// Docker's states, as GET /containers reports them: keys of the catalog, by state.
	const STATES: Record<string, string> = {
		running: 'dashboard.containers.state.running',
		exited: 'dashboard.containers.state.exited',
		stopped: 'dashboard.containers.state.stopped',
		paused: 'dashboard.containers.state.paused',
		restarting: 'dashboard.containers.state.restarting',
		created: 'dashboard.containers.state.created',
		dead: 'dashboard.containers.state.dead'
	};

	/** The label of a state in the language of the page; a state without one as it comes. */
	function stateLabel(state: string): string {
		return Object.prototype.hasOwnProperty.call(STATES, state) ? $t(STATES[state]) : state;
	}

	const running = $derived(containers.filter((c) => c.state === 'running').length);
	const stopped = $derived(containers.filter((c) => c.state !== 'running').length);
	const displayContainers = $derived(containers.slice(0, limit));

	function getStateColor(state: string): string {
		switch (state) {
			case 'running':
				return 'text-success';
			case 'exited':
			case 'stopped':
				return 'text-secondary';
			default:
				return 'text-warning';
		}
	}

	function getStateIcon(state: string) {
		switch (state) {
			case 'running':
				return Play;
			case 'exited':
			case 'stopped':
				return Square;
			default:
				return AlertCircle;
		}
	}

	function getStatusDotVariant(state: string): 'success' | 'warning' | 'error' | 'neutral' {
		switch (state) {
			case 'running':
				return 'success';
			case 'exited':
			case 'stopped':
				return 'neutral';
			default:
				return 'warning';
		}
	}

	function getWebUrl(
		container: (typeof containers)[0]
	): string | null {
		const webPort = container.ports?.find((p) => p.host_port);
		if (webPort && typeof window !== 'undefined') {
			return `http://${window.location.hostname}:${webPort.host_port}`;
		}
		return null;
	}
</script>

{#if compact}
	<div class="flex items-center gap-2 text-sm">
		<Container class="w-4 h-4 icon-container" />
		<span class="text-success">{$t('dashboard.containers.runningCount', { n: running })}</span>
		{#if stopped > 0}
			<span class="text-muted">•</span>
			<span class="text-secondary">{$t('dashboard.containers.stoppedCount', { n: stopped })}</span>
		{/if}
	</div>
{:else}
	<Card>
		<div class="card-header">
			<h3 class="card-title">
				<Container class="w-5 h-5 icon-container" />
				{$t('dashboard.containers.title')}
			</h3>
			<a href="/containers" class="text-sm text-info hover:text-status-info transition-colors"
				>{$t('dashboard.manage')}</a
			>
		</div>

		<!-- Summary Stats -->
		<div class="grid grid-cols-2 gap-3 mb-4">
			<div class="card-nested flex items-center gap-3">
				<div class="w-8 h-8 rounded-full bg-status-success/20 flex items-center justify-center">
					<CheckCircle2 class="w-4 h-4 text-success" />
				</div>
				<div>
					<p class="text-lg font-bold text-success">{running}</p>
					<p class="text-xs text-secondary">{$t('dashboard.containers.running')}</p>
				</div>
			</div>
			<div class="card-nested flex items-center gap-3">
				<div class="w-8 h-8 rounded-full bg-status-neutral/20 flex items-center justify-center">
					<XCircle class="w-4 h-4 text-secondary" />
				</div>
				<div>
					<p class="text-lg font-bold">{stopped}</p>
					<p class="text-xs text-secondary">{$t('dashboard.containers.stopped')}</p>
				</div>
			</div>
		</div>

		{#if containers.length === 0}
			<div class="text-center py-4">
				<Container class="w-8 h-8 mx-auto mb-2 text-muted opacity-50" />
				<p class="text-secondary text-sm">{$t('dashboard.containers.none')}</p>
				<a href="/containers" class="text-sm text-info hover:underline mt-1 inline-block">
					{$t('dashboard.containers.create')}
				</a>
			</div>
		{:else}
			<div class="space-y-2">
				{#each displayContainers as container}
					{@const webUrl = getWebUrl(container)}
					{@const Icon = getStateIcon(container.state)}
					<a
						href="/containers"
						class="flex items-center justify-between p-2.5 bg-surface-muted rounded-lg hover:bg-surface-overlay transition-colors group"
					>
						<div class="flex items-center gap-3 min-w-0">
							<div
								class="w-8 h-8 rounded bg-surface-overlay flex items-center justify-center flex-shrink-0"
							>
								<Icon class="w-4 h-4 {getStateColor(container.state)}" />
							</div>
							<div class="min-w-0">
								<p class="font-medium text-sm truncate">{container.name}</p>
								<p class="text-xs text-muted truncate">{container.image.split(':')[0]}</p>
							</div>
						</div>
						<div class="flex items-center gap-2">
							{#if webUrl}
								<!-- A link in the row's link: the widget is drawn in the browser only (the layout shows
								     a page once it knows the account), where no HTML parser moves it out. -->
								<!-- svelte-ignore node_invalid_placement_ssr -->
								<a
									href={webUrl}
									target="_blank"
									rel="noopener"
									onclick={(event) => event.stopPropagation()}
									class="p-1 rounded hover:bg-surface-elevated text-info opacity-0 group-hover:opacity-100 transition-opacity"
									title={$t('dashboard.containers.open')}
								>
									<ExternalLink class="w-3.5 h-3.5" />
								</a>
							{/if}
							<Badge variant={getStatusDotVariant(container.state)} class="text-xs">
								{stateLabel(container.state)}
							</Badge>
						</div>
					</a>
				{/each}
				{#if containers.length > limit}
					<a
						href="/containers"
						class="block text-xs text-info text-center py-2 hover:underline"
					>
						{$t('dashboard.containers.viewAll', { n: containers.length })}
					</a>
				{/if}
			</div>
		{/if}
	</Card>
{/if}
