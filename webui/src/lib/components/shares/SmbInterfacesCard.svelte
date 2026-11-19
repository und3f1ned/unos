<script lang="ts">
	// The network interfaces that SMB listens on (ADR-0391): every one by default, or the chosen
	// ones and the loopback. A change, and Restart SMB, restart smbd, which closes every open SMB
	// connection: both dialogs say so first. The server compares smbd's listeners with the choice;
	// its problems are shown as they come.
	import { onMount } from 'svelte';
	import { Network, RotateCw } from 'lucide-svelte';
	import {
		getSmbInterfaces,
		restartSmb,
		setSmbInterfaces,
		type SmbInterface,
		type SmbInterfaces
	} from '$lib/api/shares';
	import { Alert, Badge, Button, Card, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	let current = $state<SmbInterfaces | null>(null);
	let loadError = $state('');
	let notice = $state('');
	let warnings: string[] = $state([]);

	// The dialog of the choice: mounted while open.
	let editing = $state(false);
	let mode = $state<'every' | 'chosen'>('every');
	let picked: Record<string, boolean> = $state({});
	let saving = $state(false);
	let saveError = $state('');

	let restartOpen = $state(false);
	let restarting = $state(false);
	let restartError = $state('');

	onMount(load);

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
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
		mode = current.bound ? 'chosen' : 'every';
		picked = Object.fromEntries(current.interfaces.map((entry) => [entry.name, entry.chosen]));
		saveError = '';
		editing = true;
	}

	function closeEdit() {
		if (!saving) editing = false;
	}

	/** Whether the box of an interface can be ticked: one that Samba listens on, or one chosen before. */
	function choosable(entry: SmbInterface): boolean {
		return entry.offered || !entry.present || entry.chosen;
	}

	const selection = $derived(
		mode === 'every'
			? []
			: (current?.interfaces ?? [])
					.filter((entry) => picked[entry.name])
					.map((entry) => entry.name)
					.sort()
	);
	const unchanged = $derived(
		current !== null &&
			(mode === 'chosen') === current.bound &&
			selection.join(' ') === [...current.chosen].sort().join(' ')
	);
	const nothingPicked = $derived(mode === 'chosen' && selection.length === 0);

	function sessionsWarning(state: SmbInterfaces): string {
		if (state.sessions === null) return $t('shares.interfaces.restartWarningAny');
		if (state.sessions === 0) return $t('shares.interfaces.restartWarningNone');
		return $t('shares.interfaces.restartWarning', { n: state.sessions });
	}

	function sessionsText(state: SmbInterfaces): string {
		if (state.sessions === null) return $t('shares.interfaces.restartSessionsAny');
		if (state.sessions === 0) return $t('shares.interfaces.restartSessionsNone');
		return $t('shares.interfaces.restartSessions', { n: state.sessions });
	}

	async function save() {
		saving = true;
		saveError = '';
		try {
			const answer = await setSmbInterfaces(selection);
			current = answer;
			editing = false;
			notice =
				answer.chosen.length > 0
					? $t('shares.interfaces.savedSome', { names: answer.chosen.join(', ') })
					: $t('shares.interfaces.savedEvery');
			warnings = answer.warnings;
		} catch (e) {
			saveError = messageOf(e);
		} finally {
			saving = false;
		}
	}

	function openRestart() {
		restartError = '';
		restartOpen = true;
	}

	function closeRestart() {
		if (!restarting) restartOpen = false;
	}

	async function restart() {
		restarting = true;
		restartError = '';
		try {
			current = await restartSmb();
			restartOpen = false;
			notice = $t('shares.interfaces.restarted');
			warnings = [];
		} catch (e) {
			restartError = messageOf(e);
			await load();
		} finally {
			restarting = false;
		}
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
					<Button variant="warning" icon={RotateCw} onclick={openRestart}>
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
	<Modal open title={$t('shares.interfaces.dialogTitle')} size="lg" closable={!saving} onclose={closeEdit}>
		<div class="space-y-4">
			<fieldset class="space-y-2">
				<legend class="form-label">{$t('shares.interfaces.listenOn')}</legend>
				<label class="flex items-center gap-2 cursor-pointer">
					<input type="radio" name="smb-interfaces-mode" value="every" bind:group={mode} />
					{$t('shares.interfaces.modeEvery')}
				</label>
				<label class="flex items-center gap-2 cursor-pointer">
					<input type="radio" name="smb-interfaces-mode" value="chosen" bind:group={mode} />
					{$t('shares.interfaces.modeChosen')}
				</label>
			</fieldset>

			<fieldset class="space-y-2">
				<legend class="form-label">{$t('shares.interfaces.list')}</legend>
				{#if current.host_error}
					<Alert variant="error">{current.host_error}</Alert>
				{/if}
				{#if current.interfaces.length === 0}
					<p class="text-sm text-muted">{$t('shares.interfaces.none')}</p>
				{:else}
					<ul class="space-y-2">
						{#each current.interfaces as entry (entry.name)}
							{@const locked = mode === 'every' || !choosable(entry)}
							<li class="card-nested">
								<label class="flex items-start gap-2 {locked ? 'opacity-60' : 'cursor-pointer'}">
									<input
										type="checkbox"
										class="form-checkbox mt-1"
										bind:checked={picked[entry.name]}
										disabled={locked}
										aria-describedby="smb-interface-{entry.name}"
									/>
									<span class="min-w-0 flex flex-wrap items-center gap-2">
										<span class="mono break-all">{entry.name}</span>
										{#if entry.present}
											<Badge variant={entry.state === 'UP' ? 'success' : 'neutral'}>{entry.state}</Badge>
										{/if}
									</span>
								</label>
								<div id="smb-interface-{entry.name}" class="ml-6 mt-1 space-y-1 text-sm">
									{#if entry.present}
										<p class="mono-xs text-secondary break-all">
											{entry.addresses.length > 0 ? entry.addresses.join(', ') : $t('shares.interfaces.noAddress')}
										</p>
									{:else}
										<p class="text-warning">{$t('shares.interfaces.notOnHost')}</p>
									{/if}
									{#if entry.present && !entry.offered}
										<p class="text-muted">{$t('shares.interfaces.pointToPoint')}</p>
									{:else if entry.present && !entry.enabled}
										<p class="text-muted">{$t('shares.interfaces.disabled')}</p>
									{:else if entry.present && !entry.lan}
										<p class="text-muted">{$t('shares.interfaces.noLan')}</p>
									{/if}
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			</fieldset>

			<p class="form-hint">{$t('shares.interfaces.loopbackNote')}</p>
			<p class="form-hint">{$t('shares.interfaces.addressNote')}</p>
			<p class="form-hint">{$t('shares.interfaces.discoveryNote')}</p>

			{#if nothingPicked}
				<p class="text-sm text-warning">{$t('shares.interfaces.pickOne')}</p>
			{:else if !unchanged}
				<Alert variant="warning">{sessionsWarning(current)}</Alert>
			{/if}
		</div>

		{#if saveError}
			<Alert variant="error" title={$t('shares.interfaces.notSaved')}>{saveError}</Alert>
		{/if}

		{#snippet footer()}
			<Button variant="secondary" class="flex-1" disabled={saving} onclick={closeEdit}>
				{$t('common.cancel')}
			</Button>
			<Button class="flex-1" loading={saving} disabled={nothingPicked || unchanged} onclick={save}>
				{$t('shares.interfaces.save')}
			</Button>
		{/snippet}
	</Modal>
{/if}

<Modal
	open={restartOpen}
	title={$t('shares.interfaces.restartTitle')}
	size="md"
	danger
	closable={!restarting}
	onclose={closeRestart}
>
	{#if current}
		<p>{$t('shares.interfaces.restartText')}</p>
		<p class="mt-2">{sessionsText(current)}</p>
	{/if}
	{#if restartError}
		<Alert variant="error" title={$t('shares.interfaces.notRestarted')}>{restartError}</Alert>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={restarting} onclick={closeRestart}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={restarting} onclick={restart}>
			{$t('shares.interfaces.restartNow')}
		</Button>
	{/snippet}
</Modal>
