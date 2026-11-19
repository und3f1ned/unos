<script lang="ts">
	import { SHARE_OPTIONS, type ShareOption } from '$lib/api/shares';
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The settings of a share: whether it is listed, guest access, and the protocol options
	// (ADR-0380). What locks a box is the form's to decide.
	type Props = {
		browseable: boolean;
		guestOk: boolean;
		options: Record<ShareOption, boolean>;
		guestLocked: boolean;
		encryptionLocked: boolean;
	};

	let {
		browseable = $bindable(),
		guestOk = $bindable(),
		options = $bindable(),
		guestLocked,
		encryptionLocked
	}: Props = $props();

	// The options in the form's order, with the catalog keys of their names and of their hints.
	const OPTION_KEYS: Record<ShareOption, string> = {
		encryption_required: 'shares.form.option.encryption',
		time_machine: 'shares.form.option.timeMachine',
		previous_versions: 'shares.form.option.previousVersions'
	};
	const OPTION_HINT_KEYS: Record<ShareOption, string> = {
		encryption_required: 'shares.form.option.encryptionHint',
		time_machine: 'shares.form.option.timeMachineHint',
		previous_versions: 'shares.form.option.previousVersionsHint'
	};
	const optionChoices = $derived(
		SHARE_OPTIONS.map((name) => ({
			name,
			label: $t(OPTION_KEYS[name]),
			hint: $t(OPTION_HINT_KEYS[name]),
			locked: name === 'encryption_required' && encryptionLocked
		}))
	);
</script>

<div class="space-y-2">
	<label class="flex items-center gap-2 cursor-pointer">
		<input type="checkbox" class="form-checkbox" bind:checked={browseable} />
		{$t('shares.form.browseable')}
	</label>
	<label class="flex items-center gap-2 {guestLocked ? 'opacity-60' : 'cursor-pointer'}">
		<input type="checkbox" class="form-checkbox" bind:checked={guestOk} disabled={guestLocked} />
		{$t('shares.form.guest')}
	</label>
	{#if guestOk}
		<Alert variant="warning">{$t('shares.form.guestWarning')}</Alert>
	{/if}
</div>

<fieldset class="space-y-2">
	<legend class="form-label">{$t('shares.form.options')}</legend>
	{#each optionChoices as choice (choice.name)}
		<div>
			<label class="flex items-center gap-2 {choice.locked ? 'opacity-60' : 'cursor-pointer'}">
				<input
					type="checkbox"
					class="form-checkbox"
					bind:checked={options[choice.name]}
					disabled={choice.locked}
					aria-describedby="share-option-{choice.name}"
				/>
				{choice.label}
			</label>
			<p id="share-option-{choice.name}" class="form-hint ml-6">{choice.hint}</p>
		</div>
	{/each}
</fieldset>
