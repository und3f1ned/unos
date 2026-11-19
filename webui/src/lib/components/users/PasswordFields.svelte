<script lang="ts">
	import { Input } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { MIN_PASSWORD_LENGTH } from './password';

	type Props = {
		// The ids of the two fields.
		id: string;
		confirmId: string;
		label: string;
		confirmLabel: string;
		hint?: string;
		disabled?: boolean;
		password: string;
		confirmation: string;
	};

	let {
		id,
		confirmId,
		label,
		confirmLabel,
		hint = '',
		disabled = false,
		password = $bindable(),
		confirmation = $bindable()
	}: Props = $props();

	// The texts follow a change of the language: the markup reads $t through them.
	function passwordError(password: string): string {
		return password && password.length < MIN_PASSWORD_LENGTH
			? $t('users.password.short', { n: MIN_PASSWORD_LENGTH })
			: '';
	}

	function confirmError(password: string, confirmation: string): string {
		return confirmation && confirmation !== password ? $t('users.password.mismatch') : '';
	}
</script>

<!-- A new password and its confirmation, each with what is wrong with it. -->
<Input
	{id}
	type="password"
	{label}
	bind:value={password}
	autocomplete="new-password"
	{hint}
	error={passwordError(password)}
	{disabled}
/>
<Input
	id={confirmId}
	type="password"
	label={confirmLabel}
	bind:value={confirmation}
	autocomplete="new-password"
	error={confirmError(password, confirmation)}
	{disabled}
/>
