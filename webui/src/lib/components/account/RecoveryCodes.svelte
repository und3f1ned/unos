<script lang="ts">
	import { onMount } from 'svelte';
	import { ApiError } from '$lib/api/client';
	import { generateRecoveryCodes, getRecoveryCodesStatus, type RecoveryCodesStatus } from '$lib/api/auth';
	import { Alert, Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The recovery codes of the signed-in account, shown while two-factor
	// authentication is on (ADR-0238). Each code signs in once in place of a
	// code of the authenticator app.

	let status: RecoveryCodesStatus | null = null;
	let loading = true;
	let loadError = '';

	// The codes just generated: shown only now, until Done. The server keeps
	// their hashes and never answers them again.
	let codes: string[] = [];
	let busy = false;
	let error = '';
	// The last generation failed without a refusal (a server error, an unreadable answer or
	// none): the server may have stored new codes that never reached the page, and then the
	// saved ones no longer work. Its warning stays until a generation succeeds or is refused.
	let outcomeUnknown = false;
	let confirmNew = false;

	onMount(() => {
		void load();
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

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

	$: left = status?.remaining ?? 0;
	$: errorTitle = outcomeUnknown ? $t('account.recovery.unknown') : $t('account.recovery.refused');
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
		<Button on:click={() => (codes = [])}>{$t('common.done')}</Button>
	{:else if loading}
		<p class="text-sm text-muted">{$t('common.loading')}</p>
	{:else if loadError || !status}
		<Alert variant="error" title={$t('account.recovery.loadError')}>{loadError}</Alert>
	{:else if !status.generated}
		<p class="text-sm text-secondary">{$t('account.recovery.none')}</p>
		<Button loading={busy} on:click={generate}>{$t('account.recovery.generate')}</Button>
	{:else}
		{#if left === 0}
			<Alert variant="warning">{$t('account.recovery.noneLeft')}</Alert>
		{:else}
			<p class="text-sm">{$t('account.recovery.left', { n: left })}</p>
		{/if}
		<Button variant="secondary" loading={busy && !confirmNew} on:click={askForNewCodes}>
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
	on:close={closeConfirm}
>
	<p>{$t('account.recovery.confirmText')}</p>
	{#if error}
		<Alert variant="error" title={errorTitle}>
			{error}
			{#if outcomeUnknown}<p class="mt-1">{$t('account.recovery.unknownHint')}</p>{/if}
		</Alert>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={busy} on:click={closeConfirm}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={busy} on:click={generate}>{$t('account.recovery.generateNew')}</Button>
	</svelte:fragment>
</Modal>
