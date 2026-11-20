<script lang="ts">
	// The devices that may use the tunnel (ADR-0420), each with its address, its last handshake and
	// its traffic, with Add and Revoke.
	import { formatBytes } from '$lib/api/backup';
	import type { RemotePeer } from '$lib/api/remoteAccess';
	import { Button } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { Plus, Trash2 } from 'lucide-svelte';

	type Props = {
		peers: RemotePeer[];
		busy: boolean;
		// The units of the traffic in the language of the page (formatBytes).
		sizeUnits: string[];
		onadd: () => void;
		onrevoke: (peer: RemotePeer) => void;
	};

	let { peers, busy, sizeUnits, onadd, onrevoke }: Props = $props();
</script>

<div class="mt-6 flex flex-wrap justify-between items-center gap-2">
	<h4 class="font-medium">{$t('remote.peers')}</h4>
	<Button variant="primary" size="sm" disabled={busy} onclick={onadd}>
		<Plus class="w-4 h-4" />
		{$t('remote.addPeer')}
	</Button>
</div>
{#if peers.length === 0}
	<p class="text-sm text-content-secondary text-center py-4">{$t('remote.noPeers')}</p>
{:else}
	<ul class="mt-3 space-y-2" aria-label={$t('remote.peers')}>
		{#each peers as peer (peer.id)}
			<li class="flex items-center justify-between gap-2 p-3 bg-surface-overlay rounded-lg">
				<div class="min-w-0 break-words">
					<p class="font-medium">{peer.name} <span class="text-content-secondary font-normal">{peer.address}</span></p>
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
					size="icon"
					disabled={busy}
					aria-label={$t('remote.revokeLabel', { name: peer.name })}
					onclick={() => onrevoke(peer)}
				>
					<Trash2 class="w-4 h-4" />
				</Button>
			</li>
		{/each}
	</ul>
{/if}
