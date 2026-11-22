<script lang="ts">
	// An interface of the host on System > Network: its state, addresses and live rates, and when
	// expanded its details, traffic and Disable or Enable (ADR-0244). What ip prints (the link
	// type) is shown as it comes (ADR-0295).
	import type { NetworkInterface } from '$lib/api/network';
	import { Badge, Button } from '$lib/components/ui';
	import { formatNumber, formatRate, formatSize } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import type { NetworkInterface as WSNetworkInterface } from '$lib/stores/stats';
	import { ArrowDown, ArrowUp, Cable, ChevronDown, ChevronUp, Network, Power, PowerOff, Wifi } from 'lucide-svelte';
	import { LINK_STATES, labelOf } from './network';

	type Props = {
		iface: NetworkInterface;
		// Its counters and rates from the stats stream, if the stream has it.
		wsIface: WSNetworkInterface | undefined;
		expanded: boolean;
		// The interface being changed.
		busy: string | null;
		ontoggle: () => void;
		ondisable: () => void;
		onenable: () => void;
	};

	let { iface, wsIface, expanded, busy, ontoggle, ondisable, onenable }: Props = $props();

	let ifaceIsUp = $derived(iface.state === 'UP');

	// The speed of a link, the traffic and the rates in the units and the number format of the page:
	// a count of bytes in the largest unit that keeps it at 1 or more, with up to two decimals.
	function formatSpeed(mbps: number): string {
		return mbps >= 1000
			? $t('network.speed.gbps', { speed: formatNumber(mbps / 1000, $intlLocale, { digits: 2 }) })
			: $t('network.speed.mbps', { speed: formatNumber(mbps, $intlLocale) });
	}
	function size(bytes: number): string {
		return formatSize(bytes, $intlLocale, $t, { digits: 2 });
	}
	function rate(bytesPerSecond: number): string {
		return formatRate(bytesPerSecond, $intlLocale, $t, { digits: 2 });
	}
</script>

<section class="card p-0 overflow-hidden" aria-label={$t('network.interface.label', { name: iface.name })}>
	<!-- Interface Header -->
	<!-- The name and the live rates share a box that wraps: on a phone, or with long rates in either
	     language, the rates go below the name, and the chevron keeps its place at the end. -->
	<Button
		variant="plain"
		class="w-full p-4 flex items-center gap-3 hover:bg-surface-overlay transition-[color,background-color,border-color,text-decoration-color,fill,stroke]"
		aria-expanded={expanded}
		onclick={ontoggle}
	>
		<div class="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-6 gap-y-2">
			<div class="flex min-w-0 items-center gap-4">
				<div class="shrink-0 p-2 rounded-lg {ifaceIsUp ? 'bg-status-success/20' : 'bg-surface-overlay'}">
					{#if iface.type === 'ether' || iface.type === 'ethernet'}
						<Cable class="w-5 h-5 {ifaceIsUp ? 'text-status-success' : 'text-content-muted'}" />
					{:else if iface.type === 'wlan' || iface.name.startsWith('wlan')}
						<Wifi class="w-5 h-5 {ifaceIsUp ? 'text-status-success' : 'text-content-muted'}" />
					{:else}
						<Network class="w-5 h-5 {ifaceIsUp ? 'text-status-success' : 'text-content-muted'}" />
					{/if}
				</div>
				<div class="min-w-0 text-left">
					<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
						<span class="font-semibold text-content">{iface.name}</span>
						<Badge variant={!iface.enabled ? 'neutral' : ifaceIsUp ? 'success' : 'warning'}>
							{iface.enabled ? labelOf($t, LINK_STATES, iface.state) : $t('network.interface.disabled')}
						</Badge>
						{#if iface.speed}
							<span class="text-xs text-content-secondary">{formatSpeed(iface.speed)}</span>
						{/if}
					</div>
					<p class="text-sm text-content-secondary break-words">
						{iface.ip_addresses.length ? iface.ip_addresses[0] : $t('network.interface.noIp')}
						{#if iface.ip_addresses.length > 1}
							<span class="text-content-muted">{$t('network.more', { n: iface.ip_addresses.length - 1 })}</span>
						{/if}
					</p>
				</div>
			</div>

			<!-- Live rates from the WebSocket -->
			{#if wsIface && ifaceIsUp}
				<div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
					<span class="flex items-center gap-1 whitespace-nowrap text-content-secondary tabular">
						<ArrowDown class="w-3 h-3" aria-hidden="true" />
						{rate(wsIface.bytes_recv_rate)}
					</span>
					<span class="flex items-center gap-1 whitespace-nowrap text-content-secondary tabular">
						<ArrowUp class="w-3 h-3" aria-hidden="true" />
						{rate(wsIface.bytes_sent_rate)}
					</span>
				</div>
			{/if}
		</div>

		{#if expanded}
			<ChevronUp class="w-5 h-5 shrink-0 text-content-muted" />
		{:else}
			<ChevronDown class="w-5 h-5 shrink-0 text-content-muted" />
		{/if}
	</Button>

	<!-- Expanded Details -->
	{#if expanded}
		<div class="px-4 pb-4 border-t border-border pt-4">
			<div class="grid grid-cols-2 md:grid-cols-4 gap-4">
				<div>
					<p class="text-xs text-content-muted mb-1">{$t('network.interface.mac')}</p>
					<p class="text-sm font-mono">{iface.mac || $t('network.interface.noMac')}</p>
				</div>
				<div>
					<p class="text-xs text-content-muted mb-1">{$t('network.interface.mtu')}</p>
					<p class="text-sm">{iface.mtu}</p>
				</div>
				<div>
					<p class="text-xs text-content-muted mb-1">{$t('network.interface.type')}</p>
					<p class="text-sm">{iface.type}</p>
				</div>
				<div>
					<p class="text-xs text-content-muted mb-1">{$t('network.interface.speed')}</p>
					<p class="text-sm">{iface.speed ? formatSpeed(iface.speed) : $t('common.unknown')}</p>
				</div>
				<div class="col-span-2 md:col-span-4">
					<p class="text-xs text-content-muted mb-1">{$t('network.interface.addresses')}</p>
					{#each iface.ip_addresses as ip}
						<p class="text-sm font-mono break-all">{ip}</p>
					{:else}
						<p class="text-sm text-content-muted">{$t('network.interface.noAddresses')}</p>
					{/each}
				</div>
			</div>

			<!-- Traffic from the WebSocket -->
			{#if wsIface}
				<div class="mt-4 pt-4 border-t border-border">
					<p class="text-xs text-content-muted mb-3">{$t('network.interface.traffic')}</p>
					<div class="grid grid-cols-2 md:grid-cols-4 gap-4">
						<div>
							<p class="text-xs text-content-muted">{$t('network.interface.rxBytes')}</p>
							<p class="text-sm tabular">{size(wsIface.bytes_recv)}</p>
						</div>
						<div>
							<p class="text-xs text-content-muted">{$t('network.interface.txBytes')}</p>
							<p class="text-sm tabular">{size(wsIface.bytes_sent)}</p>
						</div>
						<div>
							<p class="text-xs text-content-muted">{$t('network.interface.rxRate')}</p>
							<p class="text-sm tabular">{rate(wsIface.bytes_recv_rate)}</p>
						</div>
						<div>
							<p class="text-xs text-content-muted">{$t('network.interface.txRate')}</p>
							<p class="text-sm tabular">{rate(wsIface.bytes_sent_rate)}</p>
						</div>
					</div>
				</div>
			{/if}

			<!-- Actions -->
			<div class="mt-4 pt-4 border-t border-border flex gap-2">
				{#if iface.enabled}
					<!-- Routine, so secondary: the dialog that asks first carries the danger. -->
					<Button variant="secondary" size="sm" icon={PowerOff} disabled={busy !== null} onclick={ondisable}>
						{$t('network.interface.disable')}
					</Button>
				{:else}
					<Button
						variant="secondary"
						size="sm"
						icon={Power}
						loading={busy === iface.name}
						disabled={busy !== null}
						onclick={onenable}
					>
						{$t('network.interface.enable')}
					</Button>
				{/if}
			</div>
		</div>
	{/if}
</section>
