<script lang="ts">
	import { onMount } from 'svelte';
	import { toDataURL } from 'qrcode';
	import {
		addRemotePeer,
		disableRemoteAccess,
		enableRemoteAccess,
		getRemoteAccess,
		revokeRemotePeer,
		setRemoteEndpoint,
		type RemotePeer,
		type RemotePeerCreated,
		type RemoteAccessStatus
	} from '$lib/api/remoteAccess';
	import { formatBytes } from '$lib/api/backup';
	import { Alert, Badge, Button, Card, Input, Modal } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { Download, Globe, Plus, Trash2 } from 'lucide-svelte';

	// System > Remote access, administrators only: the WireGuard tunnel on the host (ADR-0417). A
	// new peer's configuration comes in the answer of its creation only: it is shown once, with a
	// QR code that the browser draws, and the page keeps it only until its dialog closes.

	let status: RemoteAccessStatus | null = null;
	let loading = false;
	let loadError = '';
	let busy = false;
	let error = '';
	let success = '';

	let settings = { endpoint: '', port: '', network: '' };
	let showEnable = false;
	let showDisable = false;
	let showEndpoint = false;
	let endpointValue = '';
	let endpointError = '';
	let showAdd = false;
	let peerName = '';
	let peerKey = '';
	let addError = '';
	let revoking: RemotePeer | null = null;
	let created: RemotePeerCreated | null = null;
	let qrDataUrl = '';

	$: sizeUnits = [
		$t('remote.size.bytes'),
		$t('remote.size.kilobytes'),
		$t('remote.size.megabytes'),
		$t('remote.size.gigabytes'),
		$t('remote.size.terabytes')
	];
	$: on = !!status?.enabled;
	// A configuration without a private key: the device gave its own public key.
	// i18n-ignore: a line of WireGuard's configuration
	$: ownKey = created !== null && !created.config.includes('PrivateKey =');

	onMount(() => {
		void load();
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function load() {
		loading = true;
		try {
			status = await getRemoteAccess();
			loadError = '';
			settings = {
				endpoint: status.endpoint ?? settings.endpoint,
				port: String(status.port ?? status.defaults.port),
				network: status.network ?? status.defaults.network
			};
		} catch (e) {
			loadError = messageOf(e);
		} finally {
			loading = false;
		}
	}

	async function act(change: () => Promise<void>) {
		busy = true;
		error = '';
		success = '';
		try {
			await change();
		} catch (e) {
			error = messageOf(e);
		} finally {
			busy = false;
		}
		await load();
	}

	async function turnOn() {
		showEnable = false;
		await act(async () => {
			await enableRemoteAccess({
				endpoint: settings.endpoint.trim(),
				port: Number(settings.port),
				network: settings.network.trim()
			});
			success = $t('remote.turnedOn');
		});
	}

	async function turnOff() {
		showDisable = false;
		await act(async () => {
			await disableRemoteAccess();
			success = $t('remote.turnedOff');
		});
	}

	function openEndpoint() {
		endpointValue = status?.endpoint ?? '';
		endpointError = '';
		showEndpoint = true;
	}

	async function saveEndpoint() {
		busy = true;
		endpointError = '';
		try {
			await setRemoteEndpoint(endpointValue.trim());
			showEndpoint = false;
			success = $t('remote.endpointSaved');
		} catch (e) {
			endpointError = messageOf(e);
		} finally {
			busy = false;
		}
		await load();
	}

	function openAdd() {
		peerName = '';
		peerKey = '';
		addError = '';
		showAdd = true;
	}

	async function addPeer() {
		busy = true;
		addError = '';
		error = '';
		success = '';
		try {
			const result = await addRemotePeer(peerName.trim(), peerKey.trim() || null);
			showAdd = false;
			created = result;
			try {
				// i18n-ignore: the options of the QR code
				qrDataUrl = await toDataURL(result.config, { errorCorrectionLevel: 'M', margin: 1, width: 288 });
			} catch {
				qrDataUrl = '';
			}
		} catch (e) {
			addError = messageOf(e);
		} finally {
			busy = false;
		}
		await load();
	}

	/** The configuration is forgotten once its dialog closes. */
	function closeCreated() {
		created = null;
		qrDataUrl = '';
	}

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

	async function revoke() {
		const peer = revoking;
		revoking = null;
		if (!peer) return;
		await act(async () => {
			await revokeRemotePeer(peer.id);
			success = $t('remote.revoked', { name: peer.name });
		});
	}
</script>

<Card>
	<div class="flex flex-wrap items-center justify-between gap-2 mb-4">
		<h3 class="text-lg font-semibold flex items-center gap-2 min-w-0">
			<Globe class="w-5 h-5 shrink-0" />
			{$t('remote.title')}
		</h3>
		{#if status && !status.installed}
			<Badge variant="neutral">{$t('remote.notInstalled')}</Badge>
		{:else if status?.enabled && status.active}
			<Badge variant="success">{$t('remote.on')}</Badge>
		{:else if status?.enabled}
			<Badge variant="error">{$t('remote.down')}</Badge>
		{:else if status}
			<Badge variant="neutral">{$t('remote.off')}</Badge>
		{/if}
	</div>

	{#if success}
		<Alert variant="success" class="mb-4">{success}</Alert>
	{/if}
	{#if error}
		<Alert variant="error" class="mb-4"><p class="break-words">{error}</p></Alert>
	{/if}
	{#if loadError}
		<Alert variant="error" title={$t('remote.loadFailed')} class="mb-4">
			<p class="break-words">{loadError}</p>
			<Button variant="secondary" size="sm" class="mt-3" {loading} on:click={load}>{$t('common.retry')}</Button>
		</Alert>
	{/if}

	<p class="text-sm text-content-secondary">{$t('remote.description')}</p>

	{#if status && !status.installed}
		<Alert variant="info" class="mt-4">{$t('remote.missing')}</Alert>
	{:else if status}
		{#if status.enabled && !status.active}
			<Alert variant="warning" class="mt-4">{$t('remote.downWarning')}</Alert>
		{/if}
		{#if status.enabled && status.firewall_rule === false}
			<Alert variant="warning" class="mt-4">{$t('remote.firewallMissing')}</Alert>
		{/if}

		{#if !on}
			<form id="remote-access-settings" class="mt-4 space-y-4" on:submit|preventDefault={() => (showEnable = true)}>
				<Input
					label={$t('remote.endpoint')}
					hint={$t('remote.endpointHint')}
					placeholder={$t('remote.endpointPlaceholder')}
					bind:value={settings.endpoint}
					required
				/>
				<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
					<Input label={$t('remote.port')} hint={$t('remote.portHint')} bind:value={settings.port} required />
					<Input label={$t('remote.network')} hint={$t('remote.networkHint')} bind:value={settings.network} required />
				</div>
				<Button variant="success" type="submit" disabled={busy || !settings.endpoint.trim()}>{$t('remote.turnOn')}</Button>
			</form>
		{:else}
			<dl class="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
				<div class="min-w-0">
					<dt class="text-content-muted">{$t('remote.endpoint')}</dt>
					<dd class="font-medium break-all">{status.endpoint}</dd>
				</div>
				<div class="min-w-0">
					<dt class="text-content-muted">{$t('remote.port')}</dt>
					<dd class="font-medium">{status.port}</dd>
				</div>
				<div class="min-w-0">
					<dt class="text-content-muted">{$t('remote.network')}</dt>
					<dd class="font-medium">{status.network}</dd>
				</div>
				<div class="min-w-0">
					<dt class="text-content-muted">{$t('remote.address')}</dt>
					<dd class="font-medium">{status.address}</dd>
				</div>
				<div class="min-w-0 sm:col-span-2">
					<dt class="text-content-muted">{$t('remote.publicKey')}</dt>
					<dd class="font-mono text-xs break-all">{status.public_key}</dd>
				</div>
			</dl>
			<div class="mt-4 flex flex-wrap gap-2">
				<Button variant="secondary" disabled={busy} on:click={openEndpoint}>{$t('remote.changeEndpoint')}</Button>
				<Button variant="danger" disabled={busy} on:click={() => (showDisable = true)}>{$t('remote.turnOff')}</Button>
			</div>
			<p class="mt-4 text-sm text-content-secondary">{$t('remote.outside', { port: status.port ?? '' })}</p>
		{/if}

		{#if status.configured}
			<div class="mt-6 flex flex-wrap justify-between items-center gap-2">
				<h4 class="font-medium">{$t('remote.peers')}</h4>
				<Button variant="primary" size="sm" disabled={busy} on:click={openAdd}>
					<Plus class="w-4 h-4" />
					{$t('remote.addPeer')}
				</Button>
			</div>
			{#if status.peers.length === 0}
				<p class="text-sm text-content-secondary text-center py-4">{$t('remote.noPeers')}</p>
			{:else}
				<ul class="mt-3 space-y-2" aria-label={$t('remote.peers')}>
					{#each status.peers as peer (peer.id)}
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
								variant="ghost"
								size="icon"
								class="text-status-error hover:text-status-error"
								disabled={busy}
								aria-label={$t('remote.revokeLabel', { name: peer.name })}
								on:click={() => (revoking = peer)}
							>
								<Trash2 class="w-4 h-4" />
							</Button>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	{/if}
</Card>

<Modal open={showEnable} title={$t('remote.enableTitle')} on:close={() => (showEnable = false)}>
	<div class="space-y-3 text-sm break-words">
		<p>{$t('remote.enableNasOnly')}</p>
		<p>{$t('remote.enableRouter', { port: settings.port, endpoint: settings.endpoint.trim() })}</p>
		<p>{$t('remote.enableFirewall', { port: settings.port })}</p>
		<p>{$t('remote.enableLan')}</p>
	</div>
	<svelte:fragment slot="footer">
		<Button variant="secondary" on:click={() => (showEnable = false)}>{$t('common.cancel')}</Button>
		<Button variant="primary" disabled={busy} on:click={turnOn}>{$t('remote.turnOn')}</Button>
	</svelte:fragment>
</Modal>

<Modal open={showDisable} title={$t('remote.disableTitle')} danger on:close={() => (showDisable = false)}>
	<p class="text-sm break-words">{$t('remote.disableText')}</p>
	<svelte:fragment slot="footer">
		<Button variant="secondary" on:click={() => (showDisable = false)}>{$t('common.cancel')}</Button>
		<Button variant="danger" disabled={busy} on:click={turnOff}>{$t('remote.turnOff')}</Button>
	</svelte:fragment>
</Modal>

<Modal open={showEndpoint} title={$t('remote.endpointTitle')} closable={!busy} on:close={() => (showEndpoint = false)}>
	<form id="remote-endpoint" class="space-y-4" on:submit|preventDefault={saveEndpoint}>
		<Input
			label={$t('remote.endpoint')}
			hint={$t('remote.endpointHint')}
			placeholder={$t('remote.endpointPlaceholder')}
			bind:value={endpointValue}
			required
		/>
		<p class="text-sm text-content-secondary">{$t('remote.endpointNote')}</p>
		{#if endpointError}
			<Alert variant="error"><p class="break-words">{endpointError}</p></Alert>
		{/if}
	</form>
	<svelte:fragment slot="footer">
		<Button variant="secondary" type="button" disabled={busy} on:click={() => (showEndpoint = false)}>{$t('common.cancel')}</Button>
		<Button variant="primary" type="submit" form="remote-endpoint" disabled={busy || !endpointValue.trim()}>{$t('remote.save')}</Button>
	</svelte:fragment>
</Modal>

<Modal open={showAdd} title={$t('remote.addTitle')} closable={!busy} on:close={() => (showAdd = false)}>
	<form id="remote-add-peer" class="space-y-4" on:submit|preventDefault={addPeer}>
		<Input
			label={$t('remote.peerName')}
			placeholder={$t('remote.peerNamePlaceholder')}
			bind:value={peerName}
			maxlength={32}
			required
		/>
		<Input label={$t('remote.publicKeyLabel')} hint={$t('remote.publicKeyHint')} bind:value={peerKey} spellcheck={false} />
		{#if addError}
			<Alert variant="error"><p class="break-words">{addError}</p></Alert>
		{/if}
	</form>
	<svelte:fragment slot="footer">
		<Button variant="secondary" type="button" disabled={busy} on:click={() => (showAdd = false)}>{$t('common.cancel')}</Button>
		<Button variant="primary" type="submit" form="remote-add-peer" disabled={busy || !peerName.trim()}>
			{busy ? $t('remote.adding') : $t('remote.addPeer')}
		</Button>
	</svelte:fragment>
</Modal>

<Modal
	open={created !== null}
	title={created ? $t('remote.configTitle', { name: created.peer.name }) : ''}
	size="lg"
	on:close={closeCreated}
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
			<pre class="p-3 bg-surface-overlay rounded-lg text-xs whitespace-pre-wrap break-all" aria-label={$t('remote.configTitle', { name: created.peer.name })}>{created.config}</pre>
		</div>
	{/if}
	<svelte:fragment slot="footer">
		<Button variant="secondary" on:click={downloadConfig}>
			<Download class="w-4 h-4" />
			{$t('remote.download')}
		</Button>
		<Button variant="primary" on:click={closeCreated}>{$t('remote.done')}</Button>
	</svelte:fragment>
</Modal>

<Modal open={revoking !== null} title={$t('remote.revokeTitle')} danger on:close={() => (revoking = null)}>
	<p class="text-sm break-words">{$t('remote.revokeText', { name: revoking?.name ?? '' })}</p>
	<svelte:fragment slot="footer">
		<Button variant="secondary" on:click={() => (revoking = null)}>{$t('common.cancel')}</Button>
		<Button variant="danger" disabled={busy} on:click={revoke}>{$t('remote.revoke')}</Button>
	</svelte:fragment>
</Modal>
