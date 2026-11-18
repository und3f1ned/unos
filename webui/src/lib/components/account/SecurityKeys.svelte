<script lang="ts">
	import { onMount } from 'svelte';
	import {
		beginSecurityKeyRegistration,
		deleteSecurityKey,
		finishSecurityKeyRegistration,
		getSecurityKeys,
		type SecurityKey
	} from '$lib/api/auth';
	import { Alert, Button, Input, Modal } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { browserErrorKey, createCredential, openedByIpAddress, securityKeysSupported } from '$lib/webauthn';

	// The security keys of the signed-in account, shown while two-factor authentication is on
	// (ADR-0406). Each signs in at the code step in place of a code of the authenticator app.
	// Adding and removing one take the current password; adding one also a touch of the key.

	let keys: SecurityKey[] = [];
	let loading = true;
	let loadError = '';
	let notice = '';

	// WebAuthn needs a host name, and a browser that has it.
	let supported = true;
	let byIpAddress = false;

	let adding = false;
	let name = '';
	let password = '';
	let busy = false;
	let touching = false;
	let addError = '';

	let removing: SecurityKey | null = null;
	let removePassword = '';
	let removeError = '';

	onMount(() => {
		supported = securityKeysSupported();
		byIpAddress = openedByIpAddress(location.hostname);
		void load();
	});

	function messageOf(e: unknown): string {
		const key = browserErrorKey(e);
		if (key) return $t(key);
		return e instanceof Error ? e.message : String(e);
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
		notice = '';
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
			notice = $t('securitykeys.addedNotice', { name: key.name });
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
		notice = '';
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
			notice = $t('securitykeys.removedNotice', { name: key.name });
			busy = false;
			removing = null;
		} catch (e) {
			removeError = messageOf(e);
		} finally {
			busy = false;
		}
	}

	$: when = (value: string | null): string => formatDateTime(value, $intlLocale);
</script>

<section aria-labelledby="security-keys-title" class="mt-4 pt-4 border-t border-border space-y-3">
	<h4 id="security-keys-title" class="card-title">{$t('securitykeys.title')}</h4>
	<p class="text-sm text-content-secondary">{$t('securitykeys.hint')}</p>

	{#if notice}
		<Alert variant="success" dismissible on:dismiss={() => (notice = '')}>
			<span class="break-words">{notice}</span>
		</Alert>
	{/if}

	{#if byIpAddress}
		<Alert variant="warning">{$t('securitykeys.ipAddress')}</Alert>
	{:else if !supported}
		<Alert variant="warning">{$t('securitykeys.unsupported')}</Alert>
	{/if}

	{#if loading}
		<p class="text-sm text-content-muted">{$t('common.loading')}</p>
	{:else if loadError}
		<Alert variant="error" title={$t('securitykeys.loadError')}>{loadError}</Alert>
	{:else if keys.length === 0}
		<p class="text-sm text-content-secondary">{$t('securitykeys.none')}</p>
	{:else}
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
						on:click={() => askRemove(key)}
					>
						{$t('securitykeys.remove')}
					</Button>
				</li>
			{/each}
		</ul>
	{/if}

	{#if !byIpAddress && supported}
		<Button variant="secondary" on:click={openAdd}>{$t('securitykeys.add')}</Button>
	{/if}
</section>

<Modal open={adding} title={$t('securitykeys.addTitle')} size="md" closable={!busy} on:close={closeAdd}>
	<form id="security-key-add" class="space-y-4" on:submit|preventDefault={add}>
		<p class="text-sm text-content-secondary">{$t('securitykeys.addText')}</p>
		<Input
			id="security-key-name"
			label={$t('securitykeys.nameLabel')}
			placeholder={$t('securitykeys.namePlaceholder')}
			bind:value={name}
			maxlength={64}
			autocomplete="off"
			required
			disabled={busy}
		/>
		<Input
			id="security-key-password"
			type="password"
			label={$t('securitykeys.passwordLabel')}
			bind:value={password}
			autocomplete="current-password"
			required
			disabled={busy}
		/>
		{#if touching}
			<Alert variant="info">{$t('securitykeys.touch')}</Alert>
		{/if}
		{#if addError}
			<Alert variant="error">{addError}</Alert>
		{/if}
	</form>

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={busy} on:click={closeAdd}>{$t('common.cancel')}</Button>
		<Button
			type="submit"
			form="security-key-add"
			class="flex-1"
			loading={busy}
			disabled={!name.trim() || !password}
		>
			{$t('securitykeys.addButton')}
		</Button>
	</svelte:fragment>
</Modal>

<Modal open={removing !== null} title={$t('securitykeys.removeTitle')} size="md" closable={!busy} danger on:close={closeRemove}>
	<form id="security-key-remove" class="space-y-4" on:submit|preventDefault={remove}>
		<p class="break-words">{$t('securitykeys.removeText', { name: removing?.name ?? '' })}</p>
		<Input
			id="security-key-remove-password"
			type="password"
			label={$t('securitykeys.passwordLabel')}
			bind:value={removePassword}
			autocomplete="current-password"
			required
			disabled={busy}
		/>
		{#if removeError}
			<Alert variant="error">{removeError}</Alert>
		{/if}
	</form>

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={busy} on:click={closeRemove}>{$t('common.cancel')}</Button>
		<Button
			type="submit"
			form="security-key-remove"
			variant="danger"
			class="flex-1"
			loading={busy}
			disabled={!removePassword}
		>
			{$t('securitykeys.removeButton')}
		</Button>
	</svelte:fragment>
</Modal>
