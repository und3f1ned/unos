<script lang="ts">
	// The network interfaces that SMB listens on (ADR-0391): every one by default, or the chosen
	// ones and the loopback. A change, and Restart SMB, restart smbd, which closes every open SMB
	// connection: both dialogs say so first. The server compares smbd's listeners with the choice;
	// its problems are shown as they come.
	import { onMount } from 'svelte';
	import { Network, RotateCw } from 'lucide-svelte';
	import { getSmbInterfaces, type SmbInterfaces } from '$lib/api/shares';
	import { Alert, Button, Card } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { messageOf } from './errors';
	import SmbInterfacesDialog from './SmbInterfacesDialog.svelte';
	import SmbRestartDialog from './SmbRestartDialog.svelte';

	let current = $state<SmbInterfaces | null>(null);
	let loadError = $state('');
	let notice = $state('');
	let warnings: string[] = $state([]);

	// The dialog of the choice: mounted while open. Choose pressed again behind it starts it over.
	let editing = $state(false);
	let choiceDialog: ReturnType<typeof SmbInterfacesDialog> | undefined = $state();
	let restartDialog: ReturnType<typeof SmbRestartDialog> | undefined;

	onMount(load);

	async function load() {
		try {
			current = await getSmbInterfaces();
			loadError = '';
		} catch (e) {
			loadError = messageOf(e);
		}
	}

	/** An address of smbd's listeners, with the wildcards as words. */
	function shownAddress(address: string): string {
		if (address === '0.0.0.0') return $t('shares.interfaces.everyIpv4');
		if (address === '::') return $t('shares.interfaces.everyIpv6');
		return address;
	}

	function listeningText(state: SmbInterfaces): string {
		if (state.listening === null) return $t('shares.interfaces.listeningUnknown');
		if (state.listening.length === 0) return $t('shares.interfaces.listeningNone');
		return state.listening.map(shownAddress).join(', ');
	}

	function openEdit() {
		if (!current) return;
		choiceDialog?.reset();
		editing = true;
	}

	function saved(answer: SmbInterfaces) {
		current = answer;
		editing = false;
		notice =
			answer.chosen.length > 0
				? $t('shares.interfaces.savedSome', { names: answer.chosen.join(', ') })
				: $t('shares.interfaces.savedEvery');
		warnings = answer.warnings;
	}

	function restarted(answer: SmbInterfaces) {
		current = answer;
		notice = $t('shares.interfaces.restarted');
		warnings = [];
	}
</script>

<Card>
	<h2 class="section-title mb-4">
		<Network class="w-5 h-5" />
		{$t('shares.interfaces.title')}
	</h2>

	{#if loadError}
		<Alert variant="error" title={$t('shares.interfaces.loadError')}>{loadError}</Alert>
	{:else if current}
		<div class="space-y-3" data-testid="smb-interfaces">
			<p class="break-words">
				{#if current.chosen.length > 0}
					{$t('shares.interfaces.only', { names: current.chosen.join(', ') })}
				{:else if current.bound}
					{$t('shares.interfaces.onlyLoopback')}
				{:else}
					{$t('shares.interfaces.every')}
				{/if}
			</p>
			<p class="text-sm text-secondary">
				{$t('shares.interfaces.listening')}
				<span class="mono break-all">{listeningText(current)}</span>
			</p>
			{#if current.host_error}
				<Alert variant="error">{current.host_error}</Alert>
			{/if}
			{#if current.problems.length > 0}
				<Alert variant="warning">
					{#each current.problems as problem}
						<p class="break-words">{problem}</p>
					{/each}
				</Alert>
			{/if}
			{#if notice}
				<Alert variant="success" dismissible ondismiss={() => (notice = '')}>{notice}</Alert>
			{/if}
			{#if warnings.length > 0}
				<Alert variant="warning" dismissible ondismiss={() => (warnings = [])}>
					{#each warnings as warning}
						<p class="break-words">{warning}</p>
					{/each}
				</Alert>
			{/if}
			<div class="flex flex-wrap gap-2">
				<Button variant="secondary" icon={Network} onclick={openEdit}>
					{$t('shares.interfaces.choose')}
				</Button>
				{#if current.restart_needed}
					<Button variant="warning" icon={RotateCw} onclick={() => restartDialog?.open()}>
						{$t('shares.interfaces.restart')}
					</Button>
				{/if}
			</div>
		</div>
	{:else}
		<p class="loading-text">{$t('common.loading')}</p>
	{/if}
</Card>

{#if editing && current}
	<SmbInterfacesDialog bind:this={choiceDialog} {current} onclose={() => (editing = false)} onsaved={saved} />
{/if}

<SmbRestartDialog bind:this={restartDialog} {current} onrestarted={restarted} onfailed={load} />
