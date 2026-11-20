<script lang="ts">
	import { Container, ExternalLink } from 'lucide-svelte';
	import { Card, Badge } from '$lib/components/ui';
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
	// Stopped as the Containers page counts it, whose summary this is (ADR-0431): the exited ones.
	const stopped = $derived(containers.filter((c) => c.state === 'exited').length);
	// The paused, restarting, created and dead ones: the three counts add up to the total.
	const other = $derived(containers.length - running - stopped);
	const displayContainers = $derived(containers.slice(0, limit));

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

		<!-- The counts, which add up to the total: Other only when there is one. -->
		<dl class="grid {other > 0 ? 'grid-cols-3' : 'grid-cols-2'} gap-2 mb-3">
			<div class="px-3 py-2 rounded-lg bg-surface-muted">
				<dt class="text-xs text-secondary">{$t('dashboard.containers.running')}</dt>
				<dd class="text-xl font-semibold tabular">{running}</dd>
			</div>
			<div class="px-3 py-2 rounded-lg bg-surface-muted">
				<dt class="text-xs text-secondary">{$t('dashboard.containers.stopped')}</dt>
				<dd class="text-xl font-semibold tabular">{stopped}</dd>
			</div>
			{#if other > 0}
				<div class="px-3 py-2 rounded-lg bg-surface-muted">
					<dt class="text-xs text-secondary">{$t('dashboard.containers.other')}</dt>
					<dd class="text-xl font-semibold tabular">{other}</dd>
				</div>
			{/if}
		</dl>

		{#if containers.length === 0}
			<p class="text-secondary text-sm">{$t('dashboard.containers.none')}</p>
		{:else}
			<!-- A row per container: its name and image, then its state as a badge with a dot. No
			     state icon: the play and stop icons are the Containers page's buttons. -->
			<div class="flex flex-col">
				{#each displayContainers as container}
					{@const webUrl = getWebUrl(container)}
					<!-- A row, not a link: the card's one link is Manage, to the Containers page (ADR-0431). -->
					<div
						class="flex items-center justify-between gap-3 py-2 border-b border-border-muted last:border-b-0"
					>
						<div class="min-w-0">
							<p class="font-medium text-sm truncate" title={container.name}>{container.name}</p>
							<p class="text-xs text-muted truncate" title={container.image}>{container.image.split(':')[0]}</p>
						</div>
						<div class="flex items-center gap-2 shrink-0">
							{#if webUrl}
								<a
									href={webUrl}
									target="_blank"
									rel="noopener"
									onclick={(event) => event.stopPropagation()}
									class="p-1 rounded hover:bg-surface-hover text-content-link"
									title={$t('dashboard.containers.open')}
									aria-label={$t('dashboard.containers.open')}
								>
									<ExternalLink class="w-3.5 h-3.5" aria-hidden="true" />
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
