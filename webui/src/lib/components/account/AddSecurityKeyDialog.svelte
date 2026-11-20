<script lang="ts">
	import { Alert, Button, Input, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The dialog of a new security key (ADR-0409): its name and the current password; the card
	// asks the browser for the touch of the key.

	type Props = {
		open: boolean;
		name: string;
		password: string;
		/** The key is being added: the dialog waits and cannot be closed. */
		busy: boolean;
		/** The browser waits for the touch of the key. */
		touching: boolean;
		/** Why the key could not be added; '' otherwise. */
		error: string;
		onadd: () => void;
		onclose: () => void;
	};

	let {
		open,
		name = $bindable(),
		password = $bindable(),
		busy,
		touching,
		error,
		onadd,
		onclose
	}: Props = $props();
</script>

<Modal {open} title={$t('securitykeys.addTitle')} size="md" closable={!busy} {onclose}>
	<form
		id="security-key-add"
		class="space-y-4"
		onsubmit={(event) => {
			event.preventDefault();
			onadd();
		}}
	>
		<p class="text-sm text-content-secondary">{$t('securitykeys.addText')}</p>
		<Input
			id="security-key-name"
			label={$t('securitykeys.nameLabel')}
			placeholder={$t('securitykeys.namePlaceholder')}
			bind:value={name}
			maxlength={64}
			autocomplete="off"
			required
			disabled={busy}
		/>
		<Input
			id="security-key-password"
			type="password"
			label={$t('securitykeys.passwordLabel')}
			bind:value={password}
			autocomplete="current-password"
			required
			disabled={busy}
		/>
		{#if touching}
			<Alert variant="info">{$t('securitykeys.touch')}</Alert>
		{/if}
		{#if error}
			<Alert variant="error">{error}</Alert>
		{/if}
	</form>

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={onclose}>{$t('common.cancel')}</Button>
		<Button
			type="submit"
			form="security-key-add"
			class="flex-1"
			loading={busy}
			disabled={!name.trim() || !password}
		>
			{$t('securitykeys.addButton')}
		</Button>
	{/snippet}
</Modal>
