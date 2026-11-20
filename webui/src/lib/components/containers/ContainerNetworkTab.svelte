<script lang="ts">
	// The Ports tab of a container's drawer: each port, on the host and in the container, with its
	// protocol, one row each.
	import { Network } from 'lucide-svelte';
	import { Badge, EmptyState } from '$lib/components/ui';
	import type { Container } from '$lib/api/containers';
	import { t } from '$lib/i18n';
	import { labelOf, PROTOCOLS } from './labels';

	type Props = {
		ports: Container['ports'];
	};

	let { ports }: Props = $props();
</script>

{#if ports.length > 0}
	<table class="table" aria-label={$t('containers.drawer.ports')}>
		<thead class="table-header">
			<tr>
				<th>{$t('containers.drawer.host')}</th>
				<th>{$t('containers.drawer.container')}</th>
				<th>{$t('containers.drawer.protocol')}</th>
			</tr>
		</thead>
		<tbody>
			{#each ports as port, i (i)}
				<tr class="table-row">
					<td class="mono">{port.host_port || $t('containers.drawer.notPublished')}</td>
					<td class="mono">{port.container_port}</td>
					<td><Badge variant="neutral">{labelOf(PROTOCOLS, port.protocol, $t)}</Badge></td>
				</tr>
			{/each}
		</tbody>
	</table>
{:else}
	<EmptyState compact icon={Network} title={$t('containers.drawer.noPorts')} description={$t('containers.drawer.noPortsHint')} />
{/if}
