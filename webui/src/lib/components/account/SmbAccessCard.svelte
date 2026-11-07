<script lang="ts">
	import { onMount } from 'svelte';
	import { FolderOpen, Share2 } from 'lucide-svelte';
	import { disableMySmb, enableMySmb, getMySmb, type MySmbStatus } from '$lib/api/auth';
	import { user } from '$lib/stores/auth';
	import { Alert, Badge, Button, Card, EmptyState, Input, Modal } from '$lib/components/ui';
	import { smbUrl, windowsAddress } from '$lib/components/shares/texts';

	// SMB (network file) access of the signed-in account. An administrator
	// allows it; the user turns it on with the current password.

	// After the server's "SMB passwords must ..." answer (e.g. a password made
	// before the SMB rules, shorter than 8 characters).
	const PASSWORD_RULE_HINT =
		'Change your password above (Change Password) so that it meets this rule, then turn SMB on.';

	let status: MySmbStatus | null = null;
	let loading = true;
	let loadError = '';

	let password = '';
	let totpCode = '';
	let busy = false;
	let actionError = '';
	let notice = '';
	let warnings: string[] = [];

	let confirmOff = false;
	let offError = '';

	// The NAS's address as this browser reaches it (an IP address or a name).
	let host = '';

	onMount(() => {
		host = window.location.hostname;
		void load();
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

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
		notice = '';
		try {
			const result = await enableMySmb(password, status.totp_required ? totpCode : undefined);
			password = '';
			totpCode = '';
			notice = 'SMB is on for your account.';
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

	function closeConfirm() {
		if (!busy) {
			confirmOff = false;
			offError = '';
		}
	}

	async function turnOff() {
		if (busy) return;
		busy = true;
		offError = '';
		try {
			const result = await disableMySmb();
			confirmOff = false;
			notice = 'SMB is off for your account.';
			warnings = result?.warnings ?? [];
			actionError = '';
			await load();
		} catch (e) {
			offError = messageOf(e);
		} finally {
			busy = false;
		}
	}

	$: account = status?.account;
	$: allowed = account?.state === 'active' || account?.state === 'password_needed';
	// The SMB name is the UNOS username (set once SMB is allowed).
	$: signInName = account?.linux_name ?? $user?.username ?? '';
	// Without SMB of its own, an account can still use guest shares.
	$: usableShares = status?.shares.filter((share) => share.usable) ?? [];
	$: showConnect = allowed || usableShares.length > 0;
	$: example = (allowed ? status?.shares[0] : usableShares[0])?.name ?? 'share';
	$: mountOption = allowed ? `username=${signInName}` : 'guest';
</script>

<Card>
	<h3 class="section-title mb-4">
		<Share2 class="w-5 h-5" />
		Network file access (SMB)
	</h3>

	{#if loading}
		<p class="text-sm text-muted">Loading...</p>
	{:else if loadError || !status || !account}
		<Alert variant="error" title="SMB access could not be loaded">{loadError}</Alert>
	{:else}
		<div class="space-y-4">
			{#if notice}
				<Alert variant="success" dismissible on:dismiss={() => (notice = '')}>{notice}</Alert>
			{/if}
			{#if warnings.length > 0}
				<Alert variant="warning" dismissible on:dismiss={() => (warnings = [])}>
					{#each warnings as warning}
						<p>{warning}</p>
					{/each}
				</Alert>
			{/if}

			{#if account.state === 'unavailable'}
				<Alert variant="info">{account.reason ?? 'SMB is not available for your account.'}</Alert>
			{:else if account.state === 'off'}
				<p class="text-sm">
					SMB access is not turned on for your account. Ask an administrator to allow it.
				</p>
			{:else if account.state === 'password_needed'}
				<p class="text-sm">
					An administrator allowed SMB access. Enter your current password to turn it on: your SMB
					password is your UNOS password.
				</p>
				<form on:submit|preventDefault={turnOn} class="space-y-3 max-w-md">
					<Input
						id="smb-password"
						type="password"
						label="Password"
						bind:value={password}
						autocomplete="current-password"
					/>
					{#if status.totp_required}
						<Input
							id="smb-totp-code"
							label="Two-factor code"
							bind:value={totpCode}
							autocomplete="one-time-code"
							maxlength={16}
						/>
					{/if}
					<Button
						type="submit"
						loading={busy}
						disabled={!password || (status.totp_required && !totpCode)}
					>
						Turn on SMB
					</Button>
				</form>
			{:else if account.state === 'active'}
				<div class="flex flex-wrap items-center gap-3">
					<Badge variant="success">Active</Badge>
					<Button variant="ghost" size="sm" on:click={() => (confirmOff = true)}>Turn off SMB</Button>
				</div>
				<p class="text-sm">
					Sign in as <strong class="mono">{signInName}</strong> with your UNOS password. Changing your
					UNOS password changes it for SMB too and ends your open SMB connections.
				</p>
			{/if}

			<!-- Outside the form: it stays shown when the status read after it changed the state. -->
			{#if actionError}
				<Alert variant="error" title="SMB was not turned on">
					<p>{actionError}</p>
					{#if actionError.startsWith('SMB passwords must')}
						<p class="mt-1">{PASSWORD_RULE_HINT}</p>
					{/if}
				</Alert>
			{/if}

			<div>
				<h4 class="card-title mb-2">Shares you can use</h4>
				{#if status.shares.length === 0}
					<EmptyState icon={FolderOpen} title="No shares yet. Ask an administrator for access." />
				{:else}
					<div class="overflow-x-auto">
						<table class="table">
							<thead class="table-header">
								<tr>
									<th>Name</th>
									<th>Access</th>
									<th>Comment</th>
								</tr>
							</thead>
							<tbody>
								{#each status.shares as share (share.name)}
									<tr class="table-row align-top">
										<td>
											<span class="mono">{share.name}</span>
											{#if share.guest_ok}
												<Badge variant="neutral" class="ml-1">Guest</Badge>
											{/if}
											{#if !share.usable}
												<p class="text-xs text-muted">Not usable until SMB is on</p>
											{/if}
										</td>
										<td>{share.level === 'write' ? 'Read & write' : 'Read only'}</td>
										<td class="text-secondary">{share.comment}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</div>

			{#if showConnect}
				<div class="space-y-2">
					<h4 class="card-title">How to connect</h4>
					<ul class="space-y-1 text-sm">
						<li>
							<span class="text-secondary">Windows:</span>
							<span class="mono">{windowsAddress(host, example)}</span>
						</li>
						<li>
							<span class="text-secondary">macOS:</span> Finder → Go → Connect to Server →
							<span class="mono">{smbUrl(host, example)}</span>
						</li>
						<li>
							<span class="text-secondary">Linux:</span>
							<span class="mono">{smbUrl(host, example)}</span> or
							<span class="mono break-all"
								>sudo mount -t cifs //{host}/{example} /mnt/{example} -o {mountOption}</span
							>
						</li>
					</ul>
					<p class="text-sm text-muted">
						Two-factor authentication does not apply to SMB: use a strong password
					</p>
					<p class="text-sm text-muted">
						Use the NAS's address on your local network (IPv4, or an IPv6 ULA or link-local
						address); public addresses are refused.
					</p>
				</div>
			{/if}
		</div>
	{/if}
</Card>

<Modal open={confirmOff} title="Turn off SMB" size="md" closable={!busy} on:close={closeConfirm}>
	<p>
		Turn off SMB for your account? Your open SMB connections are ended. You can turn it on again here
		with your password.
	</p>
	{#if offError}
		<Alert variant="error" title="SMB was not turned off">{offError}</Alert>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={busy} on:click={closeConfirm}>Cancel</Button>
		<Button variant="danger" class="flex-1" loading={busy} on:click={turnOff}>Turn off SMB</Button>
	</svelte:fragment>
</Modal>
