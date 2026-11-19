<script lang="ts">
	import type { IscsiTargetCreate } from '$lib/api/iscsi';
	import type { SharePath } from '$lib/api/shares';
	import { Alert, Button, Input, Modal, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { messageOf } from './errors';
	import IscsiInitiatorsField from './IscsiInitiatorsField.svelte';
	import { lines } from './iscsi';

	// The form of a new target. Mounted while open: it starts as reset() leaves it.
	type Props = {
		/** The volumes that can hold a LUN, by "pool/volume". */
		volumes: SharePath[];
		/** A request of the tab runs: the dialog waits for it. */
		saving: boolean;
		/** Escape, the backdrop or Cancel, while nothing runs. */
		onclose: () => void;
		/** Creates the target; rejects with the server's error, which the dialog shows. */
		oncreate: (target: IscsiTargetCreate) => Promise<void>;
	};

	let { volumes, saving, onclose, oncreate }: Props = $props();

	let attempted = $state(false);
	let formError = $state('');
	let name = $state('');
	let volume = $state('');
	let size = $state('');
	let initiators = $state('');
	let chapUser = $state('');
	let chapSecret = $state('');
	let mutual = $state(false);
	let mutualSecret = $state('');

	/** Empties the form, on the first volume: when it opens, and when Add is pressed again behind it. */
	export function reset() {
		name = '';
		volume = volumes[0]?.name ?? '';
		size = '';
		initiators = '';
		chapUser = '';
		chapSecret = '';
		mutual = false;
		mutualSecret = '';
		attempted = false;
		formError = '';
	}

	reset();

	const volumeOptions = $derived(volumes.map((p) => ({ value: p.name, label: p.name })));
	const sizeNumber = $derived(Number(size));
	const nameError = $derived(attempted && !name.trim() ? $t('iscsi.form.nameRequired') : '');
	const volumeError = $derived(attempted && !volume ? $t('iscsi.form.volumeRequired') : '');
	const sizeError = $derived(
		attempted && !(Number.isInteger(sizeNumber) && sizeNumber > 0) ? $t('iscsi.form.sizeRequired') : ''
	);
	const initiatorsError = $derived(
		attempted && lines(initiators).length === 0 ? $t('iscsi.form.initiatorsRequired') : ''
	);

	async function create() {
		if (saving) return;
		attempted = true;
		formError = '';
		const number = Number(size);
		if (!name.trim() || !volume || !(Number.isInteger(number) && number > 0) || lines(initiators).length === 0) return;
		const [pool, vol] = volume.split('/');
		try {
			await oncreate({
				name: name.trim(),
				pool,
				volume: vol,
				size_gib: number,
				initiators: lines(initiators),
				chap_user: chapUser.trim() || undefined,
				chap_secret: chapSecret || undefined,
				mutual,
				mutual_secret: mutual && mutualSecret ? mutualSecret : undefined
			});
		} catch (e) {
			formError = messageOf(e);
		}
	}
</script>

<Modal open title={$t('iscsi.form.title')} size="md" closable={!saving} {onclose}>
	<form
		onsubmit={(event) => {
			event.preventDefault();
			create();
		}}
		class="space-y-4"
	>
		<Input
			id="iscsi-name"
			label={$t('iscsi.form.name')}
			bind:value={name}
			autocomplete="off"
			spellcheck={false}
			maxlength={32}
			error={nameError}
			hint={$t('iscsi.form.nameHint')}
		/>
		<Select
			id="iscsi-volume"
			label={$t('iscsi.form.volume')}
			bind:value={volume}
			options={volumeOptions}
			placeholder={$t('iscsi.form.chooseVolume')}
			error={volumeError}
		/>
		<Input
			id="iscsi-size"
			type="number"
			label={$t('iscsi.form.size')}
			bind:value={size}
			error={sizeError}
			hint={$t('iscsi.form.sizeHint')}
		/>
		<IscsiInitiatorsField
			id="iscsi-initiators"
			bind:value={initiators}
			placeholder={$t('iscsi.form.initiatorsPlaceholder')}
			error={initiatorsError}
		/>
		<Input
			id="iscsi-chap-user"
			label={$t('iscsi.form.chapUser')}
			bind:value={chapUser}
			autocomplete="off"
			spellcheck={false}
			maxlength={64}
			placeholder={name.trim()}
			hint={$t('iscsi.form.chapUserHint')}
		/>
		<Input
			id="iscsi-chap-secret"
			type="password"
			label={$t('iscsi.form.chapSecret')}
			bind:value={chapSecret}
			autocomplete="new-password"
			maxlength={16}
			hint={$t('iscsi.form.chapSecretHint')}
		/>
		<label class="flex items-center gap-2 cursor-pointer">
			<input type="checkbox" class="form-checkbox" bind:checked={mutual} />
			{$t('iscsi.form.mutual')}
		</label>
		{#if mutual}
			<Input
				id="iscsi-mutual-secret"
				type="password"
				label={$t('iscsi.form.mutualSecret')}
				bind:value={mutualSecret}
				autocomplete="new-password"
				maxlength={16}
				hint={$t('iscsi.form.mutualSecretHint')}
			/>
		{/if}
	</form>
	{#if formError}
		<Alert variant="error" title={$t('iscsi.form.notCreated')}>{formError}</Alert>
	{/if}
	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={saving} onclick={() => onclose()}>
			{$t('common.cancel')}
		</Button>
		<Button class="flex-1" loading={saving} onclick={create}>{$t('iscsi.form.create')}</Button>
	{/snippet}
</Modal>
