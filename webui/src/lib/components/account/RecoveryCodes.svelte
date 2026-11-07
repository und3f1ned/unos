<script lang="ts">
	import { onMount } from 'svelte';
	import { generateRecoveryCodes, getRecoveryCodesStatus, type RecoveryCodesStatus } from '$lib/api/auth';
	import { Alert, Button, Modal } from '$lib/components/ui';

	// The recovery codes of the signed-in account, shown while two-factor
	// authentication is on (ADR-0232). Each code signs in once in place of a
	// code of the authenticator app.

	let status: RecoveryCodesStatus | null = null;
	let loading = true;
	let loadError = '';

	// The codes just generated: shown only now, until Done. The server keeps
	// their hashes and never answers them again.
	let codes: string[] = [];
	let busy = false;
	let error = '';
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
		try {
			codes = await generateRecoveryCodes();
			// No second request: if it failed, the codes answered only now would not be shown.
			status = { remaining: codes.length, generated: true };
			loadError = '';
			confirmNew = false;
		} catch (e) {
			error = messageOf(e);
		} finally {
			busy = false;
		}
	}

	function askForNewCodes() {
		error = '';
		// Without codes left there is nothing to replace.
		if (left > 0) confirmNew = true;
		else void generate();
	}

	function closeConfirm() {
		if (!busy) {
			confirmNew = false;
			error = '';
		}
	}

	$: left = status?.remaining ?? 0;
	$: leftText = left === 1 ? '1 recovery code left' : `${left} recovery codes left`;
</script>

<section aria-labelledby="recovery-codes-title" class="mt-4 pt-4 border-t border-border space-y-3">
	<h4 id="recovery-codes-title" class="card-title">Recovery codes</h4>

	{#if codes.length > 0}
		<Alert variant="warning" title="Save these codes now">
			They are shown only this once. Keep them somewhere safe, away from this device: each code signs
			you in once in place of a code from your authenticator app.
		</Alert>
		<ul aria-label="Recovery codes" class="grid grid-cols-2 gap-2 max-w-xs mono">
			{#each codes as code}
				<li>{code}</li>
			{/each}
		</ul>
		<Button on:click={() => (codes = [])}>Done</Button>
	{:else if loading}
		<p class="text-sm text-muted">Loading...</p>
	{:else if loadError || !status}
		<Alert variant="error" title="Recovery codes could not be loaded">{loadError}</Alert>
	{:else if !status.generated}
		<p class="text-sm text-secondary">
			No recovery codes yet. If you lose your authenticator app, a recovery code signs you in once in
			its place.
		</p>
		<Button loading={busy} on:click={generate}>Generate recovery codes</Button>
	{:else}
		{#if left === 0}
			<Alert variant="warning">No recovery codes left: generate new ones.</Alert>
		{:else}
			<p class="text-sm">{leftText}</p>
		{/if}
		<Button variant="secondary" loading={busy && !confirmNew} on:click={askForNewCodes}>
			Generate new codes
		</Button>
	{/if}

	{#if error && !confirmNew}
		<Alert variant="error" title="No recovery codes were generated">{error}</Alert>
	{/if}
</section>

<Modal open={confirmNew} title="Generate new recovery codes" size="md" closable={!busy} on:close={closeConfirm}>
	<p>Your current recovery codes stop working. Ten new ones are shown once.</p>
	{#if error}
		<Alert variant="error" title="No recovery codes were generated">{error}</Alert>
	{/if}

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={busy} on:click={closeConfirm}>Cancel</Button>
		<Button class="flex-1" loading={busy} on:click={generate}>Generate new codes</Button>
	</svelte:fragment>
</Modal>
