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
	// Stopped as the Containers page counts it, whose summary this is (ADR-0430): the exited ones.
	const stopped = $derived(containers.filter((c) => c.state === 'exited').length);
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
	<Card class="h-full flex flex-col">
		<div class="card-header">
			<h3 class="card-title">
				<Container class="w-5 h-5 text-content-secondary" aria-hidden="true" />
				{$t('dashboard.containers.title')}
			</h3>
			<a href="/containers" class="link text-[13px]">{$t('dashboard.manage')}</a>
		</div>

		<!-- Summary Stats -->
		<div class="grid grid-cols-2 gap-2 mb-3">
			<div class="flex items-center gap-2.5 px-3 py-2.5 rounded-lg bg-surface-muted">
				<div class="w-8 h-8 rounded-full bg-status-success/20 flex items-center justify-center" aria-hidden="true">
					<CheckCircle2 class="w-4 h-4 text-success" />
				</div>
				<div>
					<p class="text-xl font-semibold tabular">{running}</p>
					<p class="text-xs text-secondary">{$t('dashboard.containers.running')}</p>
				</div>
			</div>
			<div class="flex items-center gap-2.5 px-3 py-2.5 rounded-lg bg-surface-muted">
				<div class="w-8 h-8 rounded-full bg-status-neutral/20 flex items-center justify-center" aria-hidden="true">
					<XCircle class="w-4 h-4 text-secondary" />
				</div>
				<div>
					<p class="text-xl font-semibold tabular">{stopped}</p>
					<p class="text-xs text-secondary">{$t('dashboard.containers.stopped')}</p>
				</div>
			</div>
		</div>

		{#if containers.length === 0}
			<div class="text-center py-4">
				<Container class="w-8 h-8 mx-auto mb-2 text-muted opacity-50" />
				<p class="text-secondary text-sm">{$t('dashboard.containers.none')}</p>
			</div>
		{:else}
			<div class="flex flex-col">
				{#each displayContainers as container}
					{@const webUrl = getWebUrl(container)}
					{@const Icon = getStateIcon(container.state)}
					<!-- A row, not a link: the card's one link is Manage, to the Containers page (ADR-0430). -->
					<div
						class="flex items-center justify-between gap-3 px-2 py-2 -mx-2 border-b border-border-muted last:border-b-0"
					>
						<div class="flex items-center gap-3 min-w-0">
							<div
								class="w-8 h-8 rounded bg-surface-overlay flex items-center justify-center flex-shrink-0"
								aria-hidden="true"
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
								<a
									href={webUrl}
									target="_blank"
									rel="noopener"
									onclick={(event) => event.stopPropagation()}
									class="p-1 rounded hover:bg-surface-elevated text-content-link"
									title={$t('dashboard.containers.open')}
								>
									<ExternalLink class="w-3.5 h-3.5" />
								</a>
							{/if}
							<Badge variant={getStatusDotVariant(container.state)} dot>
								{stateLabel(container.state)}
							</Badge>
						</div>
					</div>
				{/each}
				{#if containers.length > limit}
					<p class="text-xs text-content-muted text-center pt-2.5">
						{$t('dashboard.containers.shown', { shown: limit, n: containers.length })}
					</p>
				{/if}
			</div>
		{/if}
	</Card>
{/if}
