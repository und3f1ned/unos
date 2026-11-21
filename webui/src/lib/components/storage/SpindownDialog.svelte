<script lang="ts">
	// The idle timeout of a disk (ADR-0378), after which the disk spins down.
	import { Alert, Button, HelpText, Modal, Select } from '$lib/components/ui';
	import { setDiskSpindown, SPINDOWN_MINUTES, type Disk } from '$lib/api/storage';
	import { t } from '$lib/i18n';
	import { messageOf } from './pools';

	type Props = {
		/** The disk whose timeout is set; the dialog is open while it is set. */
		disk: Disk | null;
		/** An idle timeout in minutes, in words. */
		timeoutLabel: (minutes: number) => string;
		onclose: () => void;
		/**
		 * The timeout was saved, with the server's warning: the disk's own automatic offline scans
		 * could not be turned off for its timeout, or on again; the timeout was set all the same.
		 */
		onsaved: (warning: string) => void | Promise<void>;
	};

	let { disk, timeoutLabel, onclose, onsaved }: Props = $props();

	// The choice as text: the disk's own timeout when the dialog opens for it, then the user's.
	let choice = $derived(String(disk?.spindown ?? 0));
	let busy = $state(false);
	let error = $state('');

	let options = $derived(
		SPINDOWN_MINUTES.map((minutes) => ({ value: String(minutes), label: timeoutLabel(minutes) }))
	);

	function close() {
		if (busy) return;
		error = '';
		onclose();
	}

	async function save() {
		if (!disk) return;
		busy = true;
		error = '';
		try {
			const answer = await setDiskSpindown(disk.name, Number(choice));
			busy = false;
			error = '';
			await onsaved(answer.warning ?? '');
		} catch (e) {
			// e.g. 409 "The host has no hdparm: ..." or 500 when hdparm fails
			error = messageOf(e);
		} finally {
			busy = false;
		}
	}
</script>

<Modal open={disk !== null} title={$t('storage.spindown.title')} size="md" closable={!busy} onclose={close}>
	{#if disk}
		<form onsubmit={(e) => { e.preventDefault(); save(); }} class="space-y-4">
			<p>
				{$t('storage.spindown.disk')}
				<span class="mono break-all">{disk.path}</span>{disk.model ? ` (${disk.model})` : ''}
			</p>
			<Select
				id="spindown-timeout"
				label={$t('storage.spindown.label')}
				options={options}
				bind:value={choice}
				hint={$t('storage.spindown.hint')}
				disabled={busy}
			/>
			<HelpText text={$t('storage.spindown.scans')} />
			{#if error}
				<Alert variant="error" title={$t('storage.spindown.failed')}>{error}</Alert>
			{/if}
		</form>
	{/if}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={close}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={busy} onclick={save}>{$t('storage.spindown.save')}</Button>
	{/snippet}
</Modal>
