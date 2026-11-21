<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { onMount } from 'svelte';
	import { Share2 } from 'lucide-svelte';
	import { disableMySmb, enableMySmb, getMySmb, type MySmbStatus } from '$lib/api/auth';
	import { user } from '$lib/stores/auth';
	import { Alert, Badge, Button, Card, Input, LoadState, Skeleton, confirm, toast } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import SmbConnectHelp from './SmbConnectHelp.svelte';
	import SmbSharesTable from './SmbSharesTable.svelte';

	// SMB (network file) access of the signed-in account. An administrator
	// allows it; the user turns it on with the current password.

	let status = $state<MySmbStatus | null>(null);
	let loading = $state(true);
	let loadError = $state('');

	let password = $state('');
	let totpCode = $state('');
	let busy = $state(false);
	let actionError = $state('');
	let warnings = $state<string[]>([]);

	// The NAS's address as this browser reaches it (an IP address or a name).
	let host = $state('');

	onMount(() => {
		host = window.location.hostname;
		void load();
	});

	async function load() {
		try {
			status = await getMySmb();
			loadError = '';
		} catch (e) {
			loadError = messageOf(e);
		} finally {
			loading = false;
		}
	}

	/**
	 * Read the status again, e.g. after 2FA was turned on or off, or after
	 * the server refused to turn SMB on (the allowance or 2FA may have changed
	 * since the card was loaded). What is shown stays when it fails.
	 */
	export async function refresh(): Promise<void> {
		try {
			status = await getMySmb();
			loadError = '';
			if (!status.totp_required) totpCode = '';
		} catch {
			// Keep the status shown; the next action reads it again.
		}
	}

	async function turnOn() {
		if (!status || busy || !password) return;
		if (status.totp_required && !totpCode) return;
		busy = true;
		actionError = '';
		try {
			const result = await enableMySmb(password, status.totp_required ? totpCode : undefined);
			password = '';
			totpCode = '';
			toast.success($t('account.smb.onNotice'));
			warnings = result?.warnings ?? [];
			await load();
		} catch (e) {
			// 400 wrong password or code, 403 not allowed, 409 name collision or
			// smb.conf not UNOS's yet, 503 no Samba. The error stays shown.
			actionError = messageOf(e);
			await refresh();
		} finally {
			busy = false;
		}
	}

	async function turnOff() {
		if (busy) return;
		const yes = await confirm({
			title: $t('account.smb.offTitle'),
			message: $t('account.smb.offConfirm'),
			confirmLabel: $t('account.smb.turnOff')
		});
		if (!yes) return;
		busy = true;
		try {
			const result = await disableMySmb();
			toast.success($t('account.smb.offNotice'));
			warnings = result?.warnings ?? [];
			actionError = '';
			await load();
		} catch (e) {
			toast.error(messageOf(e), { title: $t('account.smb.notOff') });
		} finally {
			busy = false;
		}
	}

	const account = $derived(status?.account);
	const allowed = $derived(account?.state === 'active' || account?.state === 'password_needed');
	// The SMB name is the UNOS username (set once SMB is allowed).
	const signInName = $derived(account?.linux_name ?? $user?.username ?? '');
	// Without SMB of its own, an account can still use guest shares.
	const usableShares = $derived(status?.shares.filter((share) => share.usable) ?? []);
	const showConnect = $derived(allowed || usableShares.length > 0);
	// The share of the examples of how to connect.
	const example = $derived((allowed ? status?.shares[0] : usableShares[0])?.name ?? 'share');
</script>

<Card>
	<h3 class="card-title mb-4">
		<Share2 class="w-5 h-5 text-content-secondary" />
		{$t('account.smb.title')}
	</h3>

	<LoadState
		{loading}
		error={loadError || (!status || !account ? $t('account.smb.unavailable') : '')}
		errorTitle={$t('account.smb.loadError')}
		onretry={load}
	>
		{#snippet skeleton()}<Skeleton count={2} />{/snippet}
		{#if status && account}
			<div class="space-y-4">
				{#if warnings.length > 0}
					<Alert variant="warning" dismissible ondismiss={() => (warnings = [])}>
						{#each warnings as warning}
							<p>{warning}</p>
						{/each}
					</Alert>
				{/if}

				{#if account.state === 'unavailable'}
					<Alert variant="info">{account.reason ?? $t('account.smb.unavailable')}</Alert>
				{:else if account.state === 'off'}
					<p class="text-sm">{$t('account.smb.off')}</p>
				{:else if account.state === 'password_needed'}
					<p class="text-sm">{$t('account.smb.passwordNeeded')}</p>
					<form
						onsubmit={(event) => {
							event.preventDefault();
							turnOn();
						}}
						class="space-y-3 max-w-md"
					>
						<Input
							id="smb-password"
							type="password"
							label={$t('common.password')}
							bind:value={password}
							autocomplete="current-password"
						/>
						{#if status.totp_required}
							<Input
								id="smb-totp-code"
								label={$t('account.smb.totpCode')}
								bind:value={totpCode}
								autocomplete="one-time-code"
								maxlength={16}
							/>
						{/if}
						<Button type="submit" loading={busy} disabled={!password || (status.totp_required && !totpCode)}>
							{$t('account.smb.turnOn')}
						</Button>
					</form>
				{:else if account.state === 'active'}
					<div class="flex flex-wrap items-center gap-3">
						<Badge variant="success">{$t('account.smb.active')}</Badge>
						<Button variant="ghost" size="sm" loading={busy} onclick={turnOff}>
							{$t('account.smb.turnOff')}
						</Button>
					</div>
					<p class="text-sm">
						{$t('account.smb.signInBefore')} <strong class="mono">{signInName}</strong>
						{$t('account.smb.signInAfter')}
					</p>
				{/if}

				<!-- Outside the form: it stays shown when the status read after it changed the state. -->
				{#if actionError}
					<Alert variant="error" title={$t('account.smb.notOn')}>
						<p>{actionError}</p>
						<!-- After the server's "SMB passwords must ..." answer (e.g. a password made
					     before the SMB rules, shorter than 8 characters). -->
						{#if actionError.startsWith('SMB passwords must')}
							<p class="mt-1">{$t('account.smb.passwordRule')}</p>
						{/if}
					</Alert>
				{/if}

				<SmbSharesTable shares={status.shares} />

				{#if showConnect}
					<SmbConnectHelp {host} share={example} username={allowed ? signInName : null} />
				{/if}
			</div>
		{/if}
	</LoadState>
</Card>
