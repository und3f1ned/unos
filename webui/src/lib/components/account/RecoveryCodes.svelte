<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { ApiError } from '$lib/api/client';
	import { generateRecoveryCodes, getRecoveryCodesStatus, type RecoveryCodesStatus } from '$lib/api/auth';
	import { Alert, Button, ErrorState, Skeleton, confirm } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { settle, type Settled } from '$lib/pages/read';

	// The recovery codes of the signed-in account, shown while two-factor
	// authentication is on (ADR-0238). Each code signs in once in place of a
	// code of the authenticator app.

	type Props = {
		/** The count as last read, or why it could not be; null until it is read (the page reads it). */
		read: Settled<RecoveryCodesStatus> | null;
		/** A read or a generation answered. */
		onchange: (read: Settled<RecoveryCodesStatus>) => void;
	};

	let { read, onchange }: Props = $props();

	const status = $derived(read?.ok ? read.value : null);
	const loadError = $derived(read && !read.ok ? read.error : '');

	// The codes just generated: shown only now, until Done. The server keeps
	// their hashes and never answers them again.
	let codes = $state<string[]>([]);
	let busy = $state(false);
	let error = $state('');
	// The last generation failed without a refusal (a server error, an unreadable answer or
	// none): the server may have stored new codes that never reached the page, and then the
	// saved ones no longer work. Its warning stays until a generation succeeds or is refused.
	let outcomeUnknown = $state(false);

	async function load() {
		onchange(await settle(getRecoveryCodesStatus()));
	}

	async function generate() {
		if (busy) return;
		busy = true;
		error = '';
		outcomeUnknown = false;
		try {
			codes = await generateRecoveryCodes();
			// No second request: if it failed, the codes answered only now would not be shown.
			onchange({ ok: true, value: { remaining: codes.length, generated: true } });
		} catch (e) {
			error = messageOf(e);
			// Only a 4xx answer says that the server refused and changed nothing.
			outcomeUnknown = !(e instanceof ApiError && e.status < 500);
		} finally {
			busy = false;
		}
	}

	// New codes replace the current ones once confirmed; without codes left there is nothing to
	// replace. A refusal's message goes with the next try; the warning of an unknown outcome stays
	// until a generation succeeds or is refused.
	async function askForNewCodes() {
		if (busy) return;
		if (left > 0) {
			const yes = await confirm({
				title: $t('account.recovery.confirmTitle'),
				message: $t('account.recovery.confirmText'),
				confirmLabel: $t('account.recovery.generateNew')
			});
			if (!yes) return;
		}
		await generate();
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
	{:else if read === null}
		<div aria-busy="true"><Skeleton count={2} /></div>
	{:else if loadError || !status}
		<ErrorState title={$t('account.recovery.loadError')} message={loadError} onretry={load} />
	{:else if !status.generated}
		<p class="text-sm text-secondary">{$t('account.recovery.none')}</p>
		<Button loading={busy} onclick={generate}>{$t('account.recovery.generate')}</Button>
	{:else}
		{#if left === 0}
			<Alert variant="warning">{$t('account.recovery.noneLeft')}</Alert>
		{:else}
			<p class="text-sm">{$t('account.recovery.left', { n: left })}</p>
		{/if}
		<Button variant="secondary" loading={busy} onclick={askForNewCodes}>
			{$t('account.recovery.generateNew')}
		</Button>
	{/if}

	{#if error}
		<Alert variant="error" title={errorTitle}>
			{error}
			{#if outcomeUnknown}<p class="mt-1">{$t('account.recovery.unknownHint')}</p>{/if}
		</Alert>
	{/if}
</section>
