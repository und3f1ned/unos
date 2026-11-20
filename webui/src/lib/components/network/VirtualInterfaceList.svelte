<script lang="ts">
	// The bonds, VLANs and bridges that UNOS made on the host (ADR-0424): each with its kind, state,
	// ports or parent, addressing and MTU, and Delete. The state is shown as ip prints it (ADR-0295).
	import type { VirtualInterface } from '$lib/api/network';
	import { Badge, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { Trash2 } from 'lucide-svelte';
	import { labelOf } from './network';

	type Props = {
		items: VirtualInterface[];
		// Delete waits while a change runs or waits to be kept.
		disabled: boolean;
		ondelete: (item: VirtualInterface) => void;
	};

	let { items, disabled, ondelete }: Props = $props();

	const KINDS: Record<string, string> = {
		bond: 'virtual.kind.bond',
		vlan: 'virtual.kind.vlan',
		bridge: 'virtual.kind.bridge'
	};
	const MODES: Record<string, string> = { '802.3ad': 'virtual.mode.lacp', 'balance-rr': 'virtual.mode.roundRobin' };
	const ADDRESSING: Record<string, string> = {
		none: 'virtual.addressing.none',
		dhcp: 'virtual.addressing.dhcp',
		static: 'virtual.addressing.static'
	};

	function describe(item: VirtualInterface): string {
		if (item.kind === 'bond')
			return $t('virtual.bondOf', { mode: labelOf($t, MODES, item.mode), ports: item.ports.join(', ') });
		if (item.kind === 'vlan') return $t('virtual.vlanOn', { id: String(item.vlan_id), parent: item.parent ?? '' });
		return $t('virtual.bridgeOf', { ports: item.ports.length ? item.ports.join(', ') : $t('virtual.noPorts') });
	}

	function addressingLine(item: VirtualInterface): string {
		if (item.addressing === 'copied') return $t('virtual.copiedFrom', { name: item.copied_from ?? '' });
		if (item.addressing === 'static') return item.addresses.join(', ');
		return labelOf($t, ADDRESSING, item.addressing);
	}
</script>

<ul class="space-y-2" aria-label={$t('virtual.list')}>
	{#each items as item (item.name)}
		<li class="flex flex-wrap items-center justify-between gap-2 p-3 bg-surface-overlay rounded-lg">
			<div class="min-w-0">
				<div class="flex flex-wrap items-center gap-2">
					<span class="font-semibold text-content break-all">{item.name}</span>
					<Badge variant="info">{labelOf($t, KINDS, item.kind)}</Badge>
					{#if item.present}
						<Badge variant={item.state === 'UP' ? 'success' : 'warning'}>{item.state}</Badge>
					{:else}
						<Badge variant="neutral">{$t('virtual.absent')}</Badge>
					{/if}
				</div>
				<p class="text-sm text-content-secondary break-words">{describe(item)}</p>
				<p class="text-xs text-content-muted break-words">
					<span>{addressingLine(item)}</span>
					{#if item.mtu}<span class="ml-2">{$t('virtual.mtuOf', { mtu: String(item.mtu) })}</span>{/if}
				</p>
			</div>
			<Button
				variant="danger"
				size="sm"
				icon={Trash2}
				{disabled}
				aria-label={$t('virtual.deleteLabel', { name: item.name })}
				onclick={() => ondelete(item)}>{$t('virtual.delete')}</Button
			>
		</li>
	{/each}
</ul>
