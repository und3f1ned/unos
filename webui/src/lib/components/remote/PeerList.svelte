<script lang="ts">
	// The devices that may use the tunnel (ADR-0420), each with its address, its last handshake and
	// its traffic, with Add and Revoke.
	import { formatBytes } from '$lib/api/backup';
	import type { RemotePeer } from '$lib/api/remoteAccess';
	import { Badge, Button, EmptyState } from '$lib/components/ui';
	import { apiTime } from '$lib/time';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { Plus, Smartphone, Trash2 } from 'lucide-svelte';
	import { onMount } from 'svelte';

	type Props = {
		peers: RemotePeer[];
		// How far the NAS's clock is ahead of the browser's, in milliseconds (getRemoteAccess).
		clockAhead: number;
		busy: boolean;
		// The units of the traffic in the language of the page (formatBytes).
		sizeUnits: string[];
		onadd: () => void;
		onrevoke: (peer: RemotePeer) => void;
	};

	let { peers, clockAhead, busy, sizeUnits, onadd, onrevoke }: Props = $props();

	// WireGuard renews a live session's handshake every 2 minutes: a peer whose last one is younger
	// than 3 minutes on the NAS's clock is connected now. The age is read again every 15 seconds,
	// from the clock alone: the API is not asked again.
	const ONLINE_MS = 180_000;
	let now = $state(Date.now());
	onMount(() => {
		const timer = setInterval(() => (now = Date.now()), 15_000);
		return () => clearInterval(timer);
	});
	function online(peer: RemotePeer, now: number): boolean {
		const at = apiTime(peer.latest_handshake);
		return at !== null && now + clockAhead - at.getTime() < ONLINE_MS;
	}
</script>

<!-- The peers, a section of their own under the tunnel's settings. -->
<div class="mt-6 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-5">
	<h4 class="text-sm font-semibold text-content">{$t('remote.peers')}</h4>
	<Button variant="secondary" size="sm" icon={Plus} disabled={busy} onclick={onadd}>
		{$t('remote.addPeer')}
	</Button>
</div>
{#if peers.length === 0}
	<EmptyState compact icon={Smartphone} title={$t('remote.noPeers')} />
{:else}
	<ul class="mt-1 divide-y divide-border" aria-label={$t('remote.peers')}>
		{#each peers as peer (peer.id)}
			<li class="flex items-center justify-between gap-2 py-3">
				<div class="min-w-0 break-words">
					<p class="flex flex-wrap items-center gap-x-2 font-medium">
						<span>{peer.name}</span>
						<span class="text-content-secondary font-normal">{peer.address}</span>
						{#if online(peer, now)}<Badge variant="success" dot>{$t('remote.online')}</Badge>{/if}
					</p>
					<p class="text-sm text-content-secondary">
						{peer.latest_handshake
							? $t('remote.handshake', { time: formatDateTime(peer.latest_handshake, $intlLocale) })
							: $t('remote.neverConnected')}
					</p>
					{#if peer.received !== null && peer.sent !== null}
						<p class="text-sm text-content-secondary">
							{$t('remote.traffic', {
								received: formatBytes(peer.received, $intlLocale, sizeUnits),
								sent: formatBytes(peer.sent, $intlLocale, sizeUnits)
							})}
						</p>
					{/if}
				</div>
				<Button
					variant="ghost-danger"
					size="icon-sm"
					disabled={busy}
					title={$t('remote.revokeLabel', { name: peer.name })}
					aria-label={$t('remote.revokeLabel', { name: peer.name })}
					onclick={() => onrevoke(peer)}
				>
					<Trash2 class="w-4 h-4" />
				</Button>
			</li>
		{/each}
	</ul>
{/if}
