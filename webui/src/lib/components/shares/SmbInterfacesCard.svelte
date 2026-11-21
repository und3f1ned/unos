<script lang="ts">
	// The network interfaces that SMB listens on (ADR-0391): every one by default, or the chosen
	// ones and the loopback. A change, and Restart SMB, restart smbd, which closes every open SMB
	// connection: both dialogs say so first. The server compares smbd's listeners with the choice;
	// its problems are shown as they come.
	import { onMount } from 'svelte';
	import { Network, RotateCw } from 'lucide-svelte';
	import { getSmbInterfaces, type SmbInterfaces } from '$lib/api/shares';
	import { Alert, Button, Card, ErrorState, Skeleton, toast } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { messageOf } from './errors';
	import SmbInterfacesDialog from './SmbInterfacesDialog.svelte';
	import SmbRestartDialog from './SmbRestartDialog.svelte';

	let current = $state<SmbInterfaces | null>(null);
	let loadError = $state('');
	let retrying = $state(false);
	let warnings: string[] = $state([]);

	// The dialog of the choice: mounted while open. Choose pressed again behind it starts it over.
	let editing = $state(false);
	let choiceDialog: ReturnType<typeof SmbInterfacesDialog> | undefined = $state();
	let restartDialog: ReturnType<typeof SmbRestartDialog> | undefined;

	onMount(load);

	async function retry() {
		retrying = true;
		try {
			await load();
		} finally {
			retrying = false;
		}
	}

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
		toast.success(
			answer.chosen.length > 0
				? $t('shares.interfaces.savedSome', { names: answer.chosen.join(', ') })
				: $t('shares.interfaces.savedEvery')
		);
		warnings = answer.warnings;
	}

	function restarted(answer: SmbInterfaces) {
		current = answer;
		toast.success($t('shares.interfaces.restarted'));
		warnings = [];
	}
</script>

<section data-testid="smb-interfaces" aria-labelledby="smb-interfaces-title">
<Card>
	<!-- Its actions open dialogs: secondary sm in the header, beside the title. -->
	<div class="card-header flex-wrap gap-2">
		<h2 class="section-title" id="smb-interfaces-title">
			<Network class="w-5 h-5 text-content-secondary" aria-hidden="true" />
			{$t('shares.interfaces.title')}
		</h2>
		{#if current && !loadError}
			<div class="flex flex-wrap gap-2">
				{#if current.restart_needed}
					<Button variant="secondary" size="sm" icon={RotateCw} onclick={() => restartDialog?.open()}>
						{$t('shares.interfaces.restart')}
					</Button>
				{/if}
				<Button variant="secondary" size="sm" icon={Network} onclick={openEdit}>
					{$t('shares.interfaces.choose')}
				</Button>
			</div>
		{:else if !loadError}
			<!-- The button's place while loading (ADR-0430). -->
			<Skeleton shape="block" class="h-8 w-44" />
		{/if}
	</div>

	{#if loadError}
		<ErrorState title={$t('shares.interfaces.loadError')} message={loadError} onretry={retry} {retrying} />
	{:else if current}
		<div class="space-y-3">
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
				<Alert variant="warning" size="sm">
					{#each current.problems as problem}
						<p class="break-words">{problem}</p>
					{/each}
				</Alert>
			{/if}
			{#if warnings.length > 0}
				<Alert variant="warning" dismissible ondismiss={() => (warnings = [])}>
					{#each warnings as warning}
						<p class="break-words">{warning}</p>
					{/each}
				</Alert>
			{/if}
		</div>
	{:else}
		<!-- Loading (ADR-0430): the two lines, as they will be. -->
		<div class="space-y-3" aria-busy="true">
			<Skeleton size="base" class="w-3/4" />
			<Skeleton class="w-1/2" />
		</div>
	{/if}
</Card>
</section>

{#if editing && current}
	<SmbInterfacesDialog bind:this={choiceDialog} {current} onclose={() => (editing = false)} onsaved={saved} />
{/if}

<SmbRestartDialog bind:this={restartDialog} {current} onrestarted={restarted} onfailed={load} />
