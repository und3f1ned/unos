<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { Network } from 'lucide-svelte';
	import { disableWol, enableWol, type WolInterface } from '$lib/api/power';
	import { Badge, Button, Card, EmptyState, Skeleton, LoadState, HelpText } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { WakeOnLan } from './wol.svelte';

	// The host's network interfaces, each with its Wake-on-LAN and a button that turns it on or off.
	// On a phone the MAC address and the state go under the name, so that the button stays in view.
	type Props = {
		wol: WakeOnLan;
		// The interfaces cannot be read, for the reason the page shows once.
		unavailable: boolean;
		// Wake-on-LAN changed: what the page says.
		onnotice: (message: string) => void;
		// The host refused the change: why.
		onfailure: (message: string) => void;
	};

	let { wol, unavailable, onnotice, onfailure }: Props = $props();

	async function setWol(iface: WolInterface, on: boolean) {
		if (wol.busy) return;
		wol.busy = iface.name;
		try {
			const updated = on ? await enableWol(iface.name) : await disableWol(iface.name);
			wol.interfaces = wol.interfaces.map((i) => (i.name === updated.name ? updated : i));
			onnotice($t(updated.enabled ? 'power.wol.turnedOn' : 'power.wol.turnedOff', { name: updated.name }));
		} catch (e) {
			onfailure(messageOf(e));
			// Show what the host has now.
			await wol.load();
		} finally {
			wol.busy = null;
		}
	}
</script>

<!-- The state of an interface's Wake-on-LAN: a badge that reads alone. -->
{#snippet state(iface: WolInterface)}
	{#if !iface.supported}
		<Badge variant="neutral">{$t('power.wol.notSupported')}</Badge>
	{:else if iface.enabled}
		<Badge variant="success" dot>{$t('power.wol.on')}</Badge>
	{:else}
		<Badge variant="neutral">{$t('power.wol.off')}</Badge>
	{/if}
{/snippet}

<Card title={$t('power.wol.title')} description={$t('power.wol.hint')}>
	{#if unavailable}
		<p class="text-sm text-content-muted">{$t('power.unavailable')}</p>
	{:else}
		<LoadState
			loading={wol.loading}
			error={wol.error}
			errorTitle={$t('power.wol.loadError')}
			empty={wol.interfaces.length === 0}
			onretry={() => wol.load()}
		>
			{#snippet skeleton()}<Skeleton shape="row" count={2} />{/snippet}
			{#snippet emptyState()}<EmptyState compact icon={Network} title={$t('power.wol.none')} />{/snippet}
			<div class="table-frame">
				<table class="table">
					<thead class="table-header">
						<tr>
							<th>{$t('power.wol.interface')}</th>
							<th class="hidden sm:table-cell">{$t('power.wol.mac')}</th>
							<th class="hidden sm:table-cell">{$t('power.wol.title')}</th>
							<th class="w-px"><span class="sr-only">{$t('power.wol.change')}</span></th>
						</tr>
					</thead>
					<tbody>
						{#each wol.interfaces as iface (iface.name)}
							<tr class="table-row">
								<td class="align-middle">
									<span class="mono break-all">{iface.name}</span>
									<span class="mono block text-xs text-content-muted sm:hidden"
										>{iface.mac_address ?? $t('power.wol.unknownMac')}</span
									>
									<span class="mt-1 block sm:hidden">{@render state(iface)}</span>
								</td>
								<td class="mono hidden align-middle sm:table-cell">{iface.mac_address ?? $t('power.wol.unknownMac')}</td
								>
								<td class="hidden align-middle sm:table-cell">
									{@render state(iface)}
								</td>
								<td class="align-middle text-right whitespace-nowrap">
									{#if iface.supported}
										<Button
											variant="secondary"
											size="sm"
											loading={wol.busy === iface.name}
											disabled={wol.busy !== null && wol.busy !== iface.name}
											onclick={() => setWol(iface, !iface.enabled)}
										>
											{iface.enabled ? $t('power.wol.turnOff') : $t('power.wol.turnOn')}<span class="sr-only"
												>{$t('power.wol.forInterface', { name: iface.name })}</span
											>
										</Button>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<HelpText text="" moreLabel={$t('power.wol.more')} class="mt-3">
				{#snippet more()}
					<p>
						{$t('power.wol.hintBefore')}
						<span class="mono">{$t('power.wol.command')}</span>{$t('power.wol.hintAfter')}
					</p>
				{/snippet}
			</HelpText>
		</LoadState>
	{/if}
</Card>
