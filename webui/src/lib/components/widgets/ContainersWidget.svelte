<script lang="ts">
	import { Container, Play, Square, AlertCircle } from 'lucide-svelte';
	import { Card, StatusDot } from '$lib/components/ui';

	export let containers: Array<{
		id: string;
		name: string;
		state: string;
		image: string;
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

	function getStatusDotVariant(
		state: string
	): 'success' | 'warning' | 'error' | 'neutral' {
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
			<a
				href="/containers"
				class="text-sm text-info hover:text-blue-300 transition-colors">Manage →</a
			>
		</div>

		<!-- Summary -->
		<div class="flex items-center gap-4 mb-4 text-sm">
			<div class="flex items-center gap-2">
				<StatusDot status="success" />
				<span>{running} running</span>
			</div>
			<div class="flex items-center gap-2">
				<StatusDot status="neutral" />
				<span>{stopped} stopped</span>
			</div>
		</div>

		{#if containers.length === 0}
			<p class="text-secondary text-sm">No containers found</p>
		{:else}
			<div class="space-y-2">
				{#each displayContainers as container}
					<div
						class="flex items-center justify-between p-2 bg-surface-muted rounded-lg"
					>
						<div class="flex items-center gap-2">
							<svelte:component
								this={getStateIcon(container.state)}
								class="w-4 h-4 {getStateColor(container.state)}"
							/>
							<span class="font-medium text-sm">{container.name}</span>
						</div>
						<span class="text-xs text-muted truncate max-w-32"
							>{container.image.split(':')[0]}</span
						>
					</div>
				{/each}
				{#if containers.length > limit}
					<p class="text-xs text-muted text-center">
						+{containers.length - limit} more containers
					</p>
				{/if}
			</div>
		{/if}
	</Card>
{/if}
