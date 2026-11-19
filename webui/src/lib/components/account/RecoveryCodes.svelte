<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { onMount } from 'svelte';
	import { ApiError } from '$lib/api/client';
	import { generateRecoveryCodes, getRecoveryCodesStatus, type RecoveryCodesStatus } from '$lib/api/auth';
	import { Alert, Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The recovery codes of the signed-in account, shown while two-factor
	// authentication is on (ADR-0238). Each code signs in once in place of a
	// code of the authenticator app.

	let status = $state<RecoveryCodesStatus | null>(null);
	let loading = $state(true);
	let loadError = $state('');

	// The codes just generated: shown only now, until Done. The server keeps
	// their hashes and never answers them again.
	let codes = $state<string[]>([]);
	let busy = $state(false);
	let error = $state('');
	// The last generation failed without a refusal (a server error, an unreadable answer or
	// none): the server may have stored new codes that never reached the page, and then the
	// saved ones no longer work. Its warning stays until a generation succeeds or is refused.
	let outcomeUnknown = $state(false);
	let confirmNew = $state(false);

	onMount(() => {
		void load();
	});

	async function load() {
		try {
			status = await getRecoveryCodesStatus();
			loadError = '';
		} catch (e) {
			loadError = messageOf(e);
		} finally {
			loading = false;
		}
	}

	async function generate() {
		if (busy) return;
		busy = true;
		error = '';
		outcomeUnknown = false;
		try {
			codes = await generateRecoveryCodes();
			// No second request: if it failed, the codes answered only now would not be shown.
			status = { remaining: codes.length, generated: true };
			loadError = '';
			confirmNew = false;
		} catch (e) {
			error = messageOf(e);
			// Only a 4xx answer says that the server refused and changed nothing.
			outcomeUnknown = !(e instanceof ApiError && e.status < 500);
		} finally {
			busy = false;
		}
	}

	// A refusal's message goes with the dialog; the warning of an unknown outcome stays.
	function clearRefusal() {
		if (!outcomeUnknown) error = '';
	}

	function askForNewCodes() {
		clearRefusal();
		// Without codes left there is nothing to replace.
		if (left > 0) confirmNew = true;
		else void generate();
	}

	function closeConfirm() {
		if (!busy) {
			confirmNew = false;
			clearRefusal();
		}
	}

	const left = $derived(status?.remaining ?? 0);
	const errorTitle = $derived(outcomeUnknown ? $t('account.recovery.unknown') : $t('account.recovery.refused'));
</script>

<section aria-labelledby="recovery-codes-title" class="mt-4 pt-4 border-t border-border space-y-3">
	<h4 id="recovery-codes-title" class="card-title">{$t('account.recovery.title')}</h4>

	{#if codes.length > 0}
		<Alert variant="warning" title={$t('account.recovery.saveTitle')}>
			{$t('account.recovery.saveHint')}
		</Alert>
		<ul aria-label={$t('account.recovery.list')} class="grid grid-cols-2 gap-2 max-w-xs mono">
			{#each codes as code}
				<li>{code}</li>
			{/each}
		</ul>
		<Button onclick={() => (codes = [])}>{$t('common.done')}</Button>
	{:else if loading}
		<p class="text-sm text-muted">{$t('common.loading')}</p>
	{:else if loadError || !status}
		<Alert variant="error" title={$t('account.recovery.loadError')}>{loadError}</Alert>
	{:else if !status.generated}
		<p class="text-sm text-secondary">{$t('account.recovery.none')}</p>
		<Button loading={busy} onclick={generate}>{$t('account.recovery.generate')}</Button>
	{:else}
		{#if left === 0}
			<Alert variant="warning">{$t('account.recovery.noneLeft')}</Alert>
		{:else}
			<p class="text-sm">{$t('account.recovery.left', { n: left })}</p>
		{/if}
		<Button variant="secondary" loading={busy && !confirmNew} onclick={askForNewCodes}>
			{$t('account.recovery.generateNew')}
		</Button>
	{/if}

	{#if error && !confirmNew}
		<Alert variant="error" title={errorTitle}>
			{error}
			{#if outcomeUnknown}<p class="mt-1">{$t('account.recovery.unknownHint')}</p>{/if}
		</Alert>
	{/if}
</section>

<Modal
	open={confirmNew}
	title={$t('account.recovery.confirmTitle')}
	size="md"
	closable={!busy}
	onclose={closeConfirm}
>
	<p>{$t('account.recovery.confirmText')}</p>
	{#if error}
		<Alert variant="error" title={errorTitle}>
			{error}
			{#if outcomeUnknown}<p class="mt-1">{$t('account.recovery.unknownHint')}</p>{/if}
		</Alert>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={closeConfirm}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={busy} onclick={generate}>{$t('account.recovery.generateNew')}</Button>
	{/snippet}
</Modal>
