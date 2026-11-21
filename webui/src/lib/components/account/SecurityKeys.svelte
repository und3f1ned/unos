<script lang="ts">
	import { messageOf as plainMessageOf } from '$lib/errors';
	import { onMount } from 'svelte';
	import {
		beginSecurityKeyRegistration,
		deleteSecurityKey,
		finishSecurityKeyRegistration,
		getSecurityKeys,
		type SecurityKey
	} from '$lib/api/auth';
	import { Alert, Button, EmptyState, LoadState, Skeleton, toast } from '$lib/components/ui';
	import { KeyRound } from 'lucide-svelte';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { browserErrorKey, createCredential, openedByIpAddress, securityKeysSupported } from '$lib/webauthn';
	import AddSecurityKeyDialog from './AddSecurityKeyDialog.svelte';
	import RemoveSecurityKeyDialog from './RemoveSecurityKeyDialog.svelte';

	// The security keys of the signed-in account, shown while two-factor authentication is on
	// (ADR-0409). Each signs in at the code step in place of a code of the authenticator app.
	// Adding and removing one take the current password; adding one also a touch of the key.

	let keys = $state<SecurityKey[]>([]);
	let loading = $state(true);
	let loadError = $state('');

	// WebAuthn needs a host name, and a browser that has it.
	let supported = $state(true);
	let byIpAddress = $state(false);

	let adding = $state(false);
	let name = $state('');
	let password = $state('');
	let busy = $state(false);
	let touching = $state(false);
	let addError = $state('');

	let removing = $state<SecurityKey | null>(null);
	let removePassword = $state('');
	let removeError = $state('');

	onMount(() => {
		supported = securityKeysSupported();
		byIpAddress = openedByIpAddress(location.hostname);
		void load();
	});

	function messageOf(e: unknown): string {
		const key = browserErrorKey(e);
		if (key) return $t(key);
		return plainMessageOf(e);
	}

	async function load() {
		try {
			keys = await getSecurityKeys();
			loadError = '';
		} catch (e) {
			loadError = messageOf(e);
		} finally {
			loading = false;
		}
	}

	function openAdd() {
		name = '';
		password = '';
		addError = '';
		adding = true;
	}

	function closeAdd() {
		if (!busy) adding = false;
	}

	async function add() {
		if (busy || !name.trim() || !password) return;
		// The server refuses it too, but only after the touch, which leaves a passkey manager holding
		// a key the NAS never stored.
		if (keys.some((key) => key.name === name.trim())) {
			addError = $t('securitykeys.nameTaken');
			return;
		}
		busy = true;
		addError = '';
		try {
			const options = await beginSecurityKeyRegistration(password);
			touching = true;
			const answer = await createCredential(options);
			touching = false;
			const key = await finishSecurityKeyRegistration(name.trim(), answer);
			keys = [...keys, key];
			toast.success($t('securitykeys.addedNotice', { name: key.name }));
			password = '';
			busy = false;
			adding = false;
		} catch (e) {
			addError = messageOf(e);
		} finally {
			busy = false;
			touching = false;
		}
	}

	function askRemove(key: SecurityKey) {
		removePassword = '';
		removeError = '';
		removing = key;
	}

	function closeRemove() {
		if (!busy) removing = null;
	}

	async function remove() {
		if (!removing || busy || !removePassword) return;
		const key = removing;
		busy = true;
		removeError = '';
		try {
			await deleteSecurityKey(key.id, removePassword);
			keys = keys.filter((k) => k.id !== key.id);
			toast.success($t('securitykeys.removedNotice', { name: key.name }));
			busy = false;
			removing = null;
		} catch (e) {
			removeError = messageOf(e);
		} finally {
			busy = false;
		}
	}

	// Reads $intlLocale where the markup calls it: the dates follow the language.
	function when(value: string | null): string {
		return formatDateTime(value, $intlLocale);
	}
</script>

<section aria-labelledby="security-keys-title" class="mt-4 pt-4 border-t border-border space-y-3">
	<h4 id="security-keys-title" class="card-title">{$t('securitykeys.title')}</h4>
	<p class="text-sm text-content-secondary">{$t('securitykeys.hint')}</p>

	{#if byIpAddress}
		<Alert variant="warning">{$t('securitykeys.ipAddress')}</Alert>
	{:else if !supported}
		<Alert variant="warning">{$t('securitykeys.unsupported')}</Alert>
	{/if}

	<LoadState {loading} error={loadError} errorTitle={$t('securitykeys.loadError')} empty={keys.length === 0} onretry={load}>
		{#snippet skeleton()}<Skeleton count={2} />{/snippet}
		{#snippet emptyState()}<EmptyState compact icon={KeyRound} title={$t('securitykeys.none')} />{/snippet}
		<ul aria-label={$t('securitykeys.list')} class="space-y-2">
			{#each keys as key (key.id)}
				<li class="card-nested flex flex-wrap items-center justify-between gap-3">
					<div class="min-w-0">
						<p class="font-medium break-words">{key.name}</p>
						<p class="text-xs text-content-secondary break-words">
							{#if key.created_at}{$t('securitykeys.added', { date: when(key.created_at) })} · {/if}
							{key.last_used_at
								? $t('securitykeys.lastUsed', { date: when(key.last_used_at) })
								: $t('securitykeys.neverUsed')}
						</p>
						{#if key.usable && key.origin}
							<p class="text-xs text-content-muted break-all">
								{$t('securitykeys.worksAt', { origin: key.origin })}
							</p>
						{:else}
							<p class="text-xs text-status-warning">{$t('securitykeys.unusable')}</p>
						{/if}
					</div>
					<Button
						variant="danger"
						size="sm"
						aria-label={$t('securitykeys.removeLabel', { name: key.name })}
						onclick={() => askRemove(key)}
					>
						{$t('securitykeys.remove')}
					</Button>
				</li>
			{/each}
		</ul>
	</LoadState>

	{#if !byIpAddress && supported}
		<Button variant="secondary" onclick={openAdd}>{$t('securitykeys.add')}</Button>
	{/if}
</section>

<AddSecurityKeyDialog
	open={adding}
	bind:name
	bind:password
	{busy}
	{touching}
	error={addError}
	onadd={add}
	onclose={closeAdd}
/>

<RemoveSecurityKeyDialog
	key={removing}
	bind:password={removePassword}
	{busy}
	error={removeError}
	onremove={remove}
	onclose={closeRemove}
/>
