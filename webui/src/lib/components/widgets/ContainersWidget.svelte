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

	export let containers: Array<{
		id: string;
		name: string;
		state: string;
		image: string;
		ports?: Array<{ host_port: number | null; container_port: number; protocol: string }>;
	}> = [];
	export let compact: boolean = false;
	export let limit: number = 5;

	$: running = containers.filter((c) => c.state === 'running').length;
	$: stopped = containers.filter((c) => c.state !== 'running').length;
	$: displayContainers = containers.slice(0, limit);

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
		<span class="text-success">{running} running</span>
		{#if stopped > 0}
			<span class="text-muted">•</span>
			<span class="text-secondary">{stopped} stopped</span>
		{/if}
	</div>
{:else}
	<Card>
		<div class="card-header">
			<h3 class="card-title">
				<Container class="w-5 h-5 icon-container" />
				Containers
			</h3>
			<a href="/containers" class="text-sm text-info hover:text-blue-300 transition-colors"
				>Manage →</a
			>
		</div>

		<!-- Summary Stats -->
		<div class="grid grid-cols-2 gap-3 mb-4">
			<div class="card-nested flex items-center gap-3">
				<div class="w-8 h-8 rounded-full bg-green-500/20 flex items-center justify-center">
					<CheckCircle2 class="w-4 h-4 text-success" />
				</div>
				<div>
					<p class="text-lg font-bold text-success">{running}</p>
					<p class="text-xs text-secondary">Running</p>
				</div>
			</div>
			<div class="card-nested flex items-center gap-3">
				<div class="w-8 h-8 rounded-full bg-slate-500/20 flex items-center justify-center">
					<XCircle class="w-4 h-4 text-secondary" />
				</div>
				<div>
					<p class="text-lg font-bold">{stopped}</p>
					<p class="text-xs text-secondary">Stopped</p>
				</div>
			</div>
		</div>

		{#if containers.length === 0}
			<div class="text-center py-4">
				<Container class="w-8 h-8 mx-auto mb-2 text-muted opacity-50" />
				<p class="text-secondary text-sm">No containers found</p>
				<a href="/containers" class="text-sm text-info hover:underline mt-1 inline-block">
					Create your first container
				</a>
			</div>
		{:else}
			<div class="space-y-2">
				{#each displayContainers as container}
					{@const webUrl = getWebUrl(container)}
					<a
						href="/containers"
						class="flex items-center justify-between p-2.5 bg-surface-muted rounded-lg hover:bg-surface-overlay transition-colors group"
					>
						<div class="flex items-center gap-3 min-w-0">
							<div
								class="w-8 h-8 rounded bg-surface-overlay flex items-center justify-center flex-shrink-0"
							>
								<svelte:component
									this={getStateIcon(container.state)}
									class="w-4 h-4 {getStateColor(container.state)}"
								/>
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
									on:click|stopPropagation
									class="p-1 rounded hover:bg-surface-elevated text-info opacity-0 group-hover:opacity-100 transition-opacity"
									title="Open"
								>
									<ExternalLink class="w-3.5 h-3.5" />
								</a>
							{/if}
							<Badge variant={getStatusDotVariant(container.state)} class="text-xs">
								{container.state}
							</Badge>
						</div>
					</a>
				{/each}
				{#if containers.length > limit}
					<a
						href="/containers"
						class="block text-xs text-info text-center py-2 hover:underline"
					>
						View all {containers.length} containers →
					</a>
				{/if}
			</div>
		{/if}
	</Card>
{/if}
