<script lang="ts">
	import { SHARE_OPTIONS, type ShareOption } from '$lib/api/shares';
	import { Alert, Checkbox, HelpText } from '$lib/components/ui';
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
	// One line under each option, and the whole of it under Details.
	const OPTION_HINT_KEYS: Record<ShareOption, string> = {
		encryption_required: 'shares.form.option.encryptionHint',
		time_machine: 'shares.form.option.timeMachineHint',
		previous_versions: 'shares.form.option.previousVersionsHint'
	};
	const OPTION_MORE_KEYS: Record<ShareOption, string> = {
		encryption_required: 'shares.form.option.encryptionMore',
		time_machine: 'shares.form.option.timeMachineMore',
		previous_versions: 'shares.form.option.previousVersionsMore'
	};
	const optionChoices = $derived(
		SHARE_OPTIONS.map((name) => ({
			name,
			label: $t(OPTION_KEYS[name]),
			hint: $t(OPTION_HINT_KEYS[name]),
			more: $t(OPTION_MORE_KEYS[name]),
			locked: name === 'encryption_required' && encryptionLocked
		}))
	);
</script>

<div class="space-y-2">
	<Checkbox bind:checked={browseable} label={$t('shares.form.browseable')} />
	<Checkbox bind:checked={guestOk} disabled={guestLocked} label={$t('shares.form.guest')} />
	{#if guestOk}
		<!-- The one line that matters, and the server's whole warning (ADR-0096) under its details. -->
		<Alert variant="warning" size="sm" title={$t('shares.form.guestShort')}>
			{#snippet details()}
				<p>{$t('shares.form.guestWarning')}</p>
			{/snippet}
		</Alert>
	{/if}
</div>

<fieldset class="space-y-2">
	<legend class="form-label">{$t('shares.form.options')}</legend>
	{#each optionChoices as choice (choice.name)}
		<div>
			<Checkbox
				bind:checked={options[choice.name]}
				disabled={choice.locked}
				label={choice.label}
				describedby="share-option-{choice.name}"
			/>
			<!-- One line under each option, and the whole of it under Details. -->
			<div id="share-option-{choice.name}" class="ml-7">
				<HelpText text={choice.hint}>
					{#snippet more()}<p>{choice.more}</p>{/snippet}
				</HelpText>
			</div>
		</div>
	{/each}
</fieldset>
