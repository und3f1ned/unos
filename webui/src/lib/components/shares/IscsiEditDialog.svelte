<script lang="ts">
	import { untrack } from 'svelte';
	import type { IscsiTarget, IscsiTargetChange } from '$lib/api/iscsi';
	import { Alert, Button, Checkbox, Input, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { messageOf } from './errors';
	import IscsiInitiatorsField from './IscsiInitiatorsField.svelte';
	import { lines } from './iscsi';

	// The change of a target's initiators and secrets. Mounted while open: it starts from the
	// target as reset() fills it.
	type Props = {
		target: IscsiTarget;
		/** A request of the tab runs: the dialog waits for it. */
		saving: boolean;
		/** Escape, the backdrop or Cancel, while nothing runs. */
		onclose: () => void;
		/** Changes the target; rejects with the server's error, which the dialog shows. */
		onsave: (change: IscsiTargetChange) => Promise<void>;
	};

	let { target, saving, onclose, onsave }: Props = $props();

	let editInitiators = $state('');
	let newSecret = $state(false);
	let editSecret = $state('');
	let editMutual = $state(false);
	let newMutualSecret = $state(false);
	let editMutualSecret = $state('');
	let editError = $state('');

	/** Fills the form from `from`: when it opens, and when Change is pressed again behind it. */
	export function reset(from: IscsiTarget) {
		editInitiators = from.initiators.join('\n');
		newSecret = false;
		editSecret = '';
		editMutual = from.mutual;
		newMutualSecret = false;
		editMutualSecret = '';
		editError = '';
	}

	untrack(() => reset(target));

	async function saveEdit() {
		if (saving) return;
		editError = '';
		const turnedOn = editMutual && !target.mutual;
		try {
			await onsave({
				initiators: lines(editInitiators),
				new_secret: newSecret,
				chap_secret: newSecret && editSecret ? editSecret : undefined,
				mutual: editMutual,
				new_mutual_secret: editMutual && newMutualSecret,
				mutual_secret: editMutual && (newMutualSecret || turnedOn) && editMutualSecret ? editMutualSecret : undefined
			});
		} catch (e) {
			editError = messageOf(e);
		}
	}
</script>

<Modal open title={$t('iscsi.edit.title', { name: target.name })} size="md" closable={!saving} {onclose}>
	<form
		onsubmit={(event) => {
			event.preventDefault();
			saveEdit();
		}}
		class="space-y-4"
	>
		<IscsiInitiatorsField id="iscsi-edit-initiators" bind:value={editInitiators} />
		<Checkbox bind:checked={newSecret} label={$t('iscsi.edit.newSecret')} />
		{#if newSecret}
			<Input
				id="iscsi-edit-secret"
				type="password"
				label={$t('iscsi.form.chapSecret')}
				bind:value={editSecret}
				autocomplete="new-password"
				maxlength={16}
				hint={$t('iscsi.edit.newSecretHint')}
			/>
		{/if}
		<Checkbox bind:checked={editMutual} label={$t('iscsi.edit.mutual')} />
		{#if editMutual && target.mutual}
			<Checkbox bind:checked={newMutualSecret} label={$t('iscsi.edit.newMutualSecret')} />
		{/if}
		{#if editMutual && (newMutualSecret || !target.mutual)}
			<Input
				id="iscsi-edit-mutual-secret"
				type="password"
				label={$t('iscsi.form.mutualSecret')}
				bind:value={editMutualSecret}
				autocomplete="new-password"
				maxlength={16}
				hint={$t('iscsi.form.mutualSecretHint')}
			/>
		{/if}
	</form>
	{#if editError}
		<Alert variant="error" title={$t('iscsi.edit.notChanged')}>{editError}</Alert>
	{/if}
	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={saving} onclick={() => onclose()}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={saving} onclick={saveEdit}>{$t('iscsi.edit.save')}</Button>
	{/snippet}
</Modal>
