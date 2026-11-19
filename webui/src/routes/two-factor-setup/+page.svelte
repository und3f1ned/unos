<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { toDataURL } from 'qrcode';
	import { enableTOTP, setupTOTP, type TOTPSetupResponse } from '$lib/api/auth';
	import { isSetupOnly, loadCurrentUser, signOut as endSession, user } from '$lib/stores/auth';
	import { connectWebSocket, disconnectWebSocket } from '$lib/stores/stats';
	import { Alert, Button, Card, Input } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { CheckCircle, ShieldCheck } from 'lucide-svelte';

	// The page of an account without two-factor authentication while every account must have it
	// (ADR-0386). During the account's grace period the sign-in leads here, and Later goes on to the
	// dashboard; after it, this is the one page the account gets, and the server refuses it
	// everything but the setup, its status and the sign-out.

	let setup = $state<TOTPSetupResponse | null>(null);
	// The otpauth:// URI holds the secret: the QR code is drawn in the browser (ADR-0027).
	let qrDataUrl = $state('');
	let code = $state('');
	let error = $state('');
	let busy = $state(false);
	let done = $state(false);

	const required = $derived($user?.two_factor_required ?? null);
	const setupOnly = $derived(isSetupOnly($user));
	const deadline = $derived(
		required ? formatDateTime(required.by, $intlLocale, { dateStyle: 'long', timeStyle: 'short' }) : ''
	);
	// Nothing to set up: no requirement applies to this account, or no longer. Before the markup
	// is drawn, also the first time; whoever changes the account ($user) tells it.
	$effect.pre(() => {
		if ($user && !required && !done) untrack(() => goto('/', { replaceState: true }));
	});

	async function start() {
		busy = true;
		error = '';
		try {
			setup = await setupTOTP();
			try {
				// i18n-ignore: the options of the QR code
				qrDataUrl = await toDataURL(setup.qr_uri, { width: 384, margin: 2, errorCorrectionLevel: 'M' });
			} catch {
				// The secret is still shown for manual entry.
				qrDataUrl = '';
			}
		} catch (e) {
			error = messageOf(e) || $t('account.totp.setupFailed');
		} finally {
			busy = false;
		}
	}

	async function confirm() {
		busy = true;
		error = '';
		try {
			await enableTOTP(code);
		} catch (e) {
			error = messageOf(e) || $t('account.totp.invalidCode');
			busy = false;
			return;
		}
		done = true;
		setup = null;
		qrDataUrl = '';
		code = '';
		try {
			// The account has its rights again: the menu and the live data come back.
			await loadCurrentUser();
			connectWebSocket();
		} catch {
			// A reload of the page loads the account again.
		} finally {
			busy = false;
		}
	}

	function signOut() {
		disconnectWebSocket();
		void endSession();
		goto('/login');
	}
</script>

<div class="page-container max-w-xl space-y-6">
	<h1 class="page-title flex items-center gap-2">
		<ShieldCheck class="w-6 h-6 shrink-0" />
		{$t('twofactor.title')}
	</h1>

	{#if done}
		<Card class="space-y-4">
			<p class="flex items-center gap-2 font-medium text-status-success">
				<CheckCircle class="w-5 h-5 shrink-0" />
				{$t('twofactor.done')}
			</p>
			<p class="text-sm text-content-secondary">{$t('twofactor.doneHint')}</p>
			<div class="flex flex-wrap gap-3">
				<Button href="/system#security">{$t('twofactor.recoveryCodes')}</Button>
				<Button variant="secondary" href="/">{$t('twofactor.continue')}</Button>
			</div>
		</Card>
	{:else if required}
		<Card class="space-y-4">
			{#if setupOnly}
				<Alert variant="warning">{$t('twofactor.setupOnly')}</Alert>
			{:else}
				<p>{$t('twofactor.due', { date: deadline })}</p>
			{/if}
			<p class="text-sm text-content-secondary">{$t('twofactor.appHint')}</p>

			{#if error}
				<Alert variant="error">{error}</Alert>
			{/if}

			{#if setup}
				<p class="text-sm text-content-secondary">{$t('account.totp.scan')}</p>
				{#if qrDataUrl}
					<div class="flex justify-center">
						<div class="bg-content-on-brand p-2 rounded-lg">
							<img src={qrDataUrl} alt={$t('account.totp.qrAlt')} class="w-48 h-48" />
						</div>
					</div>
				{/if}
				<div class="text-center min-w-0">
					<p class="text-xs text-content-muted mb-2">{$t('account.totp.manual')}</p>
					<code class="bg-surface-muted px-3 py-1 rounded font-mono text-sm break-all">{setup.secret}</code>
				</div>
				<form
					class="space-y-4"
					onsubmit={(event) => {
						event.preventDefault();
						confirm();
					}}
				>
					<Input
						id="two-factor-code"
						label={$t('account.totp.verifyLabel')}
						bind:value={code}
						autocomplete="one-time-code"
						maxlength={6}
						required
					/>
					<div class="flex flex-wrap gap-3">
						<Button type="submit" loading={busy} disabled={busy || code.length !== 6}
							>{$t('account.totp.enable2fa')}</Button
						>
						{#if setupOnly}
							<Button variant="secondary" onclick={signOut}>{$t('twofactor.signOut')}</Button>
						{:else}
							<Button variant="secondary" href="/">{$t('twofactor.later')}</Button>
						{/if}
					</div>
				</form>
			{:else}
				<div class="flex flex-wrap gap-3">
					<Button loading={busy} disabled={busy} onclick={start}>{$t('twofactor.start')}</Button>
					{#if setupOnly}
						<Button variant="secondary" onclick={signOut}>{$t('twofactor.signOut')}</Button>
					{:else}
						<Button variant="secondary" href="/">{$t('twofactor.later')}</Button>
					{/if}
				</div>
			{/if}
		</Card>
	{/if}
</div>
