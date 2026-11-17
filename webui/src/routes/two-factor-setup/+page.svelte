<script lang="ts">
	import { goto } from '$app/navigation';
	import { toDataURL } from 'qrcode';
	import { enableTOTP, logout, setupTOTP, type TOTPSetupResponse } from '$lib/api/auth';
	import { clearCurrentUser, isSetupOnly, loadCurrentUser, user } from '$lib/stores/auth';
	import { connectWebSocket, disconnectWebSocket } from '$lib/stores/stats';
	import { Alert, Button, Card, Input } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { CheckCircle, ShieldCheck } from 'lucide-svelte';

	// The page of an account without two-factor authentication while every account must have it
	// (ADR-0383). During the account's grace period the sign-in leads here, and Later goes on to the
	// dashboard; after it, this is the one page the account gets, and the server refuses it
	// everything but the setup, its status and the sign-out.

	let setup: TOTPSetupResponse | null = null;
	// The otpauth:// URI holds the secret: the QR code is drawn in the browser (ADR-0027).
	let qrDataUrl = '';
	let code = '';
	let error = '';
	let busy = false;
	let done = false;

	$: required = $user?.two_factor_required ?? null;
	$: setupOnly = isSetupOnly($user);
	$: deadline = required
		? new Date(required.by).toLocaleString($intlLocale, { dateStyle: 'long', timeStyle: 'short' })
		: '';
	// Nothing to set up: no requirement applies to this account, or no longer.
	$: if ($user && !required && !done) goto('/', { replaceState: true });

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

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
		void logout();
		disconnectWebSocket();
		clearCurrentUser();
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
						<div class="bg-white p-2 rounded-lg">
							<img src={qrDataUrl} alt={$t('account.totp.qrAlt')} class="w-48 h-48" />
						</div>
					</div>
				{/if}
				<div class="text-center min-w-0">
					<p class="text-xs text-content-muted mb-2">{$t('account.totp.manual')}</p>
					<code class="bg-surface-muted px-3 py-1 rounded font-mono text-sm break-all">{setup.secret}</code>
				</div>
				<form class="space-y-4" on:submit|preventDefault={confirm}>
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
							<Button variant="secondary" on:click={signOut}>{$t('twofactor.signOut')}</Button>
						{:else}
							<Button variant="secondary" href="/">{$t('twofactor.later')}</Button>
						{/if}
					</div>
				</form>
			{:else}
				<div class="flex flex-wrap gap-3">
					<Button loading={busy} disabled={busy} on:click={start}>{$t('twofactor.start')}</Button>
					{#if setupOnly}
						<Button variant="secondary" on:click={signOut}>{$t('twofactor.signOut')}</Button>
					{:else}
						<Button variant="secondary" href="/">{$t('twofactor.later')}</Button>
					{/if}
				</div>
			{/if}
		</Card>
	{/if}
</div>
