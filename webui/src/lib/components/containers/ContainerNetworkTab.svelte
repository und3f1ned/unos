<script lang="ts">
	// The Network tab of a container's drawer: each port, on the host and in the container.
	import { Network } from 'lucide-svelte';
	import { Badge } from '$lib/components/ui';
	import type { Container } from '$lib/api/containers';
	import { t } from '$lib/i18n';
	import { labelOf, PROTOCOLS } from './labels';

	type Props = {
		ports: Container['ports'];
	};

	let { ports }: Props = $props();
</script>

<div class="space-y-4">
	{#if ports.length > 0}
		<div class="space-y-3">
			<div class="flex items-center gap-2 mb-2">
				<Network class="w-4 h-4 text-secondary" />
				<span class="text-secondary text-sm font-medium">{$t('containers.drawer.ports')}</span>
			</div>
			{#each ports as port}
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
					<Badge variant="info">{labelOf(PROTOCOLS, port.protocol, $t)}</Badge>
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
