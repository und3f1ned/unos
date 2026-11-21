<script lang="ts">
	// A new peer's configuration, shown once (ADR-0420): with a QR code that the browser drew, and a
	// download as a .conf file. The card forgets it when the dialog closes.
	import type { RemotePeerCreated } from '$lib/api/remoteAccess';
	import { Alert, Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { Download } from 'lucide-svelte';

	type Props = {
		// The answer of the peer's creation; null while the dialog is closed.
		created: RemotePeerCreated | null;
		// The configuration as a QR code (a data: URL), or "" where it could not be drawn.
		qrDataUrl: string;
		onclose: () => void;
	};

	let { created, qrDataUrl, onclose }: Props = $props();

	// A configuration without a private key: the device gave its own public key.
	// i18n-ignore: a line of WireGuard's configuration
	let ownKey = $derived(created !== null && !created.config.includes('PrivateKey ='));

	function downloadConfig() {
		if (!created) return;
		const blob = new Blob([created.config], { type: 'text/plain' });
		const url = URL.createObjectURL(blob);
		const link = document.createElement('a');
		link.href = url;
		// i18n-ignore: a file name
		link.download = `${created.peer.name.replace(/[^A-Za-z0-9._-]+/g, '-') || 'peer'}.conf`;
		link.click();
		// Later: some browsers start the download only after the click's event.
		setTimeout(() => URL.revokeObjectURL(url), 60_000);
	}
</script>

<Modal
	open={created !== null}
	title={created ? $t('remote.configTitle', { name: created.peer.name }) : ''}
	size="lg"
	{onclose}
>
	{#if created}
		<div class="space-y-4 text-sm">
			<Alert variant="warning">{$t('remote.configOnce')}</Alert>
			{#if ownKey}
				<Alert variant="info">{$t('remote.configOwnKey')}</Alert>
			{/if}
			{#if qrDataUrl}
				<div class="flex justify-center">
					<img src={qrDataUrl} alt={$t('remote.qrAlt', { name: created.peer.name })} class="w-64 h-64 max-w-full" />
				</div>
			{/if}
			<pre
				class="p-3 bg-surface-overlay rounded-lg text-xs whitespace-pre-wrap break-all"
				aria-label={$t('remote.configTitle', { name: created.peer.name })}>{created.config}</pre>
		</div>
	{/if}
	{#snippet footer()}
		<Button variant="secondary" onclick={downloadConfig}>
			<Download class="w-4 h-4" />
			{$t('remote.download')}
		</Button>
		<Button variant="primary" onclick={onclose}>{$t('remote.done')}</Button>
	{/snippet}
</Modal>
