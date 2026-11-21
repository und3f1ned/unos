<script lang="ts">
	import { messageOf } from '$lib/errors';
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
	import { Alert, Badge, Button, Card, Input, confirm, toast, HelpText } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import FormDialog from './FormDialog.svelte';
	import PeerConfigDialog from './PeerConfigDialog.svelte';
	import PeerList from './PeerList.svelte';

	// System > Remote access, administrators only: the WireGuard tunnel on the host (ADR-0420). A
	// new peer's configuration comes in the answer of its creation only: it is shown once, with a
	// QR code that the browser draws, and the page keeps it only until its dialog closes.

	let status = $state<RemoteAccessStatus | null>(null);
	// How far the NAS's clock is ahead of the browser's (getRemoteAccess): a peer's handshake ages on it.
	let clockAhead = $state(0);
	let loading = $state(false);
	let loadError = $state('');
	let busy = $state(false);
	let error = $state('');

	let settings = $state({ endpoint: '', port: '', network: '' });
	// Turn on stays enabled and says, at the field, what it waits for.
	let settingsError = $state('');
	let showEndpoint = $state(false);
	let endpointValue = $state('');
	let endpointError = $state('');
	let showAdd = $state(false);
	let peerName = $state('');
	let peerKey = $state('');
	let addError = $state('');
	let created = $state<RemotePeerCreated | null>(null);
	let qrDataUrl = $state('');

	let sizeUnits = $derived([
		$t('remote.size.bytes'),
		$t('remote.size.kilobytes'),
		$t('remote.size.megabytes'),
		$t('remote.size.gigabytes'),
		$t('remote.size.terabytes')
	]);
	let on = $derived(!!status?.enabled);

	onMount(() => {
		void load();
	});

	async function load() {
		loading = true;
		try {
			const answer = await getRemoteAccess();
			status = answer.status;
			clockAhead = answer.clockAhead;
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
		try {
			await change();
		} catch (e) {
			error = messageOf(e);
		} finally {
			busy = false;
		}
		await load();
	}

	/** Turn the tunnel on once confirmed: what reaches the NAS, and what the router and firewall need. */
	async function turnOn() {
		const yes = await confirm({
			title: $t('remote.enableTitle'),
			message: $t('remote.enableNasOnly'),
			consequences: [
				$t('remote.enableRouter', { port: settings.port, endpoint: settings.endpoint.trim() }),
				$t('remote.enableFirewall', { port: settings.port }),
				$t('remote.enableLan')
			],
			confirmLabel: $t('remote.turnOn'),
			tone: 'warning'
		});
		if (!yes) return;
		await act(async () => {
			await enableRemoteAccess({
				endpoint: settings.endpoint.trim(),
				port: Number(settings.port),
				network: settings.network.trim()
			});
			toast.success($t('remote.turnedOn'));
		});
	}

	async function turnOff() {
		const yes = await confirm({
			title: $t('remote.disableTitle'),
			message: $t('remote.disableText'),
			confirmLabel: $t('remote.turnOff')
		});
		if (!yes) return;
		await act(async () => {
			await disableRemoteAccess();
			toast.success($t('remote.turnedOff'));
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
			toast.success($t('remote.endpointSaved'));
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

	async function revoke(peer: RemotePeer) {
		const yes = await confirm({
			title: $t('remote.revokeTitle', { name: peer.name }),
			message: $t('remote.revokeText', { name: peer.name }),
			confirmLabel: $t('remote.revoke')
		});
		if (!yes) return;
		await act(async () => {
			await revokeRemotePeer(peer.id);
			toast.success($t('remote.revoked', { name: peer.name }));
		});
	}
</script>

<!-- The header: the title with its state, and while the tunnel is on its two actions,
     secondary; the colour of danger is Turn off's dialog's. The state comes first, the settings
     under it, the peers last, each part once. -->
<Card>
	<div class="card-header">
		<div class="flex min-w-0 flex-wrap items-center gap-2">
			<h3 class="card-title min-w-0">{$t('remote.title')}</h3>
			{#if status && !status.installed}
				<Badge variant="neutral">{$t('remote.notInstalled')}</Badge>
			{:else if status?.enabled && status.active}
				<Badge variant="success" dot>{$t('remote.on')}</Badge>
			{:else if status?.enabled}
				<Badge variant="error" dot>{$t('remote.down')}</Badge>
			{:else if status}
				<Badge variant="neutral">{$t('remote.off')}</Badge>
			{/if}
		</div>
		{#if status?.installed && on}
			<div class="flex flex-wrap gap-2">
				<Button variant="secondary" size="sm" disabled={busy} onclick={openEndpoint}>{$t('remote.changeEndpoint')}</Button>
				<Button variant="secondary" size="sm" disabled={busy} onclick={turnOff}>{$t('remote.turnOff')}</Button>
			</div>
		{/if}
	</div>

	{#if error}
		<Alert variant="error" class="mb-4"><p class="break-words">{error}</p></Alert>
	{/if}
	{#if loadError}
		<Alert variant="error" title={$t('remote.loadFailed')} class="mb-4">
			<p class="break-words">{loadError}</p>
			<Button variant="secondary" size="sm" class="mt-3" {loading} onclick={load}>{$t('common.retry')}</Button>
		</Alert>
	{/if}

	<HelpText text={$t('remote.summary')} moreLabel={$t('remote.more')}>
		{#snippet more()}
			<p>{$t('remote.description')}</p>
			{#if status?.port}<p>{$t('remote.outside', { port: status.port })}</p>{/if}
		{/snippet}
	</HelpText>

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
			<form
				id="remote-access-settings"
				class="mt-4 space-y-4"
				novalidate
				onsubmit={(e) => {
					e.preventDefault();
					settingsError = settings.endpoint.trim() ? '' : $t('remote.endpointNeeded');
					if (!settingsError) void turnOn();
				}}
			>
				<Input
					label={$t('remote.endpoint')}
					hint={$t('remote.endpointHint')}
					placeholder={$t('remote.endpointPlaceholder')}
					bind:value={settings.endpoint}
					error={settings.endpoint.trim() ? '' : settingsError}
					required
				/>
				<!-- The port is a short number: a compact field beside the network's. -->
				<div class="grid grid-cols-1 gap-4 sm:grid-cols-[9rem_minmax(0,1fr)]">
					<Input label={$t('remote.port')} hint={$t('remote.portHint')} bind:value={settings.port} required />
					<Input label={$t('remote.network')} hint={$t('remote.networkHint')} bind:value={settings.network} required />
				</div>
				<Button variant="primary" type="submit" disabled={busy}>{$t('remote.turnOn')}</Button>
			</form>
		{:else}
			<dl class="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
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
		{/if}

		{#if status.configured}
			<PeerList peers={status.peers} {clockAhead} {busy} {sizeUnits} onadd={openAdd} onrevoke={revoke} />
		{/if}
	{/if}
</Card>

<FormDialog
	open={showEndpoint}
	title={$t('remote.endpointTitle')}
	formId="remote-endpoint"
	{busy}
	error={endpointError}
	submitLabel={$t('remote.save')}
	submitDisabled={busy || !endpointValue.trim()}
	oncancel={() => (showEndpoint = false)}
	onsubmit={saveEndpoint}
>
	<Input
		label={$t('remote.endpoint')}
		hint={$t('remote.endpointHint')}
		placeholder={$t('remote.endpointPlaceholder')}
		bind:value={endpointValue}
		required
	/>
	<p class="text-sm text-content-secondary">{$t('remote.endpointNote')}</p>
</FormDialog>

<FormDialog
	open={showAdd}
	title={$t('remote.addTitle')}
	formId="remote-add-peer"
	{busy}
	error={addError}
	submitLabel={$t('remote.addPeer')}
	submitDisabled={busy || !peerName.trim()}
	oncancel={() => (showAdd = false)}
	onsubmit={addPeer}
>
	<Input
		label={$t('remote.peerName')}
		placeholder={$t('remote.peerNamePlaceholder')}
		bind:value={peerName}
		maxlength={32}
		required
	/>
	<Input label={$t('remote.publicKeyLabel')} hint={$t('remote.publicKeyHint')} bind:value={peerKey} spellcheck={false} />
</FormDialog>

<PeerConfigDialog {created} {qrDataUrl} onclose={closeCreated} />

