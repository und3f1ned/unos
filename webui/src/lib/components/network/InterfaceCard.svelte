<script lang="ts">
	// An interface of the host on System > Network: its state, addresses and live rates, and when
	// expanded its details, traffic and Disable or Enable (ADR-0244). What ip prints (the link
	// type) is shown as it comes (ADR-0295).
	import type { NetworkInterface } from '$lib/api/network';
	import { Badge, Button } from '$lib/components/ui';
	import { formatNumber, scaleBytes } from '$lib/format';
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

	// The units of a count of bytes, one for each power of 1024: keys of the catalog.
	const BYTE_UNITS = [
		'network.bytes.b',
		'network.bytes.kb',
		'network.bytes.mb',
		'network.bytes.gb',
		'network.bytes.tb'
	];

	let ifaceIsUp = $derived(iface.state === 'UP');

	// The speed of a link in the units and the number format of the page.
	function formatSpeed(mbps: number): string {
		return mbps >= 1000
			? $t('network.speed.gbps', { speed: (mbps / 1000).toLocaleString($intlLocale) })
			: $t('network.speed.mbps', { speed: mbps.toLocaleString($intlLocale) });
	}

	// The traffic of the stats stream in the units and the number format of the page: a count of
	// bytes in the largest unit that keeps it at 1 or more, with up to two decimals, as formatBytes
	// of $lib/stores/stats writes it in English.
	function formatSize(bytes: number): string {
		const { value, power } = scaleBytes(bytes, BYTE_UNITS.length);
		return $t('network.bytes.size', { value: formatNumber(value, $intlLocale, 2), unit: $t(BYTE_UNITS[power]) });
	}

	function formatRate(bytesPerSecond: number): string {
		return $t('network.bytes.perSecond', { size: formatSize(bytesPerSecond) });
	}
</script>

<section class="card p-0 overflow-hidden" aria-label={$t('network.interface.label', { name: iface.name })}>
	<!-- Interface Header -->
	<Button
		variant="plain"
		class="w-full p-4 flex items-center justify-between hover:bg-surface-overlay transition-[color,background-color,border-color,text-decoration-color,fill,stroke]"
		aria-expanded={expanded}
		onclick={ontoggle}
	>
		<div class="flex items-center gap-4">
			<div class="p-2 rounded-lg {ifaceIsUp ? 'bg-status-success/20' : 'bg-surface-overlay'}">
				{#if iface.type === 'ether' || iface.type === 'ethernet'}
					<Cable class="w-5 h-5 {ifaceIsUp ? 'text-status-success' : 'text-content-muted'}" />
				{:else if iface.type === 'wlan' || iface.name.startsWith('wlan')}
					<Wifi class="w-5 h-5 {ifaceIsUp ? 'text-status-success' : 'text-content-muted'}" />
				{:else}
					<Network class="w-5 h-5 {ifaceIsUp ? 'text-status-success' : 'text-content-muted'}" />
				{/if}
			</div>
			<div class="text-left">
				<div class="flex items-center gap-2">
					<span class="font-semibold text-content">{iface.name}</span>
					<Badge variant={!iface.enabled ? 'neutral' : ifaceIsUp ? 'success' : 'warning'}>
						{iface.enabled ? labelOf($t, LINK_STATES, iface.state) : $t('network.interface.disabled')}
					</Badge>
					{#if iface.speed}
						<span class="text-xs text-content-secondary">{formatSpeed(iface.speed)}</span>
					{/if}
				</div>
				<p class="text-sm text-content-secondary">
					{iface.ip_addresses.length ? iface.ip_addresses[0] : $t('network.interface.noIp')}
					{#if iface.ip_addresses.length > 1}
						<span class="text-content-muted">{$t('network.more', { n: iface.ip_addresses.length - 1 })}</span>
					{/if}
				</p>
			</div>
		</div>

		<div class="flex items-center gap-6">
			<!-- Live rates from the WebSocket -->
			{#if wsIface && ifaceIsUp}
				<div class="flex items-center gap-4 text-sm">
					<span class="flex items-center gap-1 text-content-secondary tabular">
						<ArrowDown class="w-3 h-3" aria-hidden="true" />
						{formatRate(wsIface.bytes_recv_rate)}
					</span>
					<span class="flex items-center gap-1 text-content-secondary tabular">
						<ArrowUp class="w-3 h-3" aria-hidden="true" />
						{formatRate(wsIface.bytes_sent_rate)}
					</span>
				</div>
			{/if}

			{#if expanded}
				<ChevronUp class="w-5 h-5 text-content-muted" />
			{:else}
				<ChevronDown class="w-5 h-5 text-content-muted" />
			{/if}
		</div>
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
							<p class="text-sm tabular">{formatSize(wsIface.bytes_recv)}</p>
						</div>
						<div>
							<p class="text-xs text-content-muted">{$t('network.interface.txBytes')}</p>
							<p class="text-sm tabular">{formatSize(wsIface.bytes_sent)}</p>
						</div>
						<div>
							<p class="text-xs text-content-muted">{$t('network.interface.rxRate')}</p>
							<p class="text-sm tabular">{formatRate(wsIface.bytes_recv_rate)}</p>
						</div>
						<div>
							<p class="text-xs text-content-muted">{$t('network.interface.txRate')}</p>
							<p class="text-sm tabular">{formatRate(wsIface.bytes_sent_rate)}</p>
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
