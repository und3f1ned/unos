<script lang="ts">
	import { untrack } from 'svelte';
	import { setSmbInterfaces, type SmbInterface, type SmbInterfaces } from '$lib/api/shares';
	import { Alert, Badge, Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { messageOf } from './errors';

	// The choice of the interfaces that SMB listens on (ADR-0391). Mounted while open: it starts
	// from the server's choice as reset() takes it.
	type Props = {
		/** The interfaces and the choice as the server reports them. */
		current: SmbInterfaces;
		/** Escape, the backdrop or Cancel, while nothing is being saved. */
		onclose: () => void;
		/** The server took the choice: its answer. */
		onsaved: (answer: SmbInterfaces) => void;
	};

	let { current, onclose, onsaved }: Props = $props();

	let mode = $state<'every' | 'chosen'>('every');
	let picked: Record<string, boolean> = $state({});
	let saving = $state(false);
	let saveError = $state('');

	/** The server's choice: when the dialog opens, and when Choose is pressed again behind it. */
	export function reset() {
		mode = current.bound ? 'chosen' : 'every';
		picked = Object.fromEntries(current.interfaces.map((entry) => [entry.name, entry.chosen]));
		saveError = '';
	}

	untrack(reset);

	function close() {
		if (!saving) onclose();
	}

	/** Whether the box of an interface can be ticked: one that Samba listens on, or one chosen before. */
	function choosable(entry: SmbInterface): boolean {
		return entry.offered || !entry.present || entry.chosen;
	}

	const selection = $derived(
		mode === 'every'
			? []
			: current.interfaces
					.filter((entry) => picked[entry.name])
					.map((entry) => entry.name)
					.sort()
	);
	const unchanged = $derived(
		(mode === 'chosen') === current.bound && selection.join(' ') === [...current.chosen].sort().join(' ')
	);
	const nothingPicked = $derived(mode === 'chosen' && selection.length === 0);

	function sessionsWarning(state: SmbInterfaces): string {
		if (state.sessions === null) return $t('shares.interfaces.restartWarningAny');
		if (state.sessions === 0) return $t('shares.interfaces.restartWarningNone');
		return $t('shares.interfaces.restartWarning', { n: state.sessions });
	}

	async function save() {
		saving = true;
		saveError = '';
		try {
			onsaved(await setSmbInterfaces(selection));
		} catch (e) {
			saveError = messageOf(e);
		} finally {
			saving = false;
		}
	}
</script>

<Modal open title={$t('shares.interfaces.dialogTitle')} size="lg" closable={!saving} onclose={close}>
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

		<!-- What else to know, on demand: one line of help, not three paragraphs. -->
		<details class="form-hint">
			<summary class="cursor-pointer text-content-secondary">{$t('shares.interfaces.more')}</summary>
			<div class="mt-1 space-y-1">
				<p>{$t('shares.interfaces.loopbackNote')}</p>
				<p>{$t('shares.interfaces.addressNote')}</p>
				<p>{$t('shares.interfaces.discoveryNote')}</p>
			</div>
		</details>

		{#if nothingPicked}
			<p id="smb-interfaces-pick-one" class="text-sm text-warning">{$t('shares.interfaces.pickOne')}</p>
		{:else if !unchanged}
			<Alert variant="warning">{sessionsWarning(current)}</Alert>
		{/if}
	</div>

	{#if saveError}
		<Alert variant="error" title={$t('shares.interfaces.notSaved')}>{saveError}</Alert>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={saving} onclick={close}>
			{$t('common.cancel')}
		</Button>
		<!-- Disabled only with nothing to save, and then it says why. -->
		<Button
			class="flex-1"
			loading={saving}
			disabled={nothingPicked || unchanged}
			title={unchanged && !nothingPicked ? $t('shares.interfaces.noChanges') : undefined}
			aria-describedby={nothingPicked ? 'smb-interfaces-pick-one' : undefined}
			onclick={save}
		>
			{$t('shares.interfaces.save')}
		</Button>
	{/snippet}
</Modal>
