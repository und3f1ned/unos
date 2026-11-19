<script lang="ts">
	import type { SecurityKey } from '$lib/api/auth';
	import { Alert, Button, Input, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// Asks the current password before a security key is removed (ADR-0409).

	type Props = {
		/** The key to remove; the dialog is closed without one. */
		key: SecurityKey | null;
		password: string;
		/** The key is being removed: the dialog waits and cannot be closed. */
		busy: boolean;
		/** Why the key could not be removed; '' otherwise. */
		error: string;
		onremove: () => void;
		onclose: () => void;
	};

	let { key, password = $bindable(), busy, error, onremove, onclose }: Props = $props();
</script>

<Modal open={key !== null} title={$t('securitykeys.removeTitle')} size="md" closable={!busy} danger {onclose}>
	<form
		id="security-key-remove"
		class="space-y-4"
		onsubmit={(event) => {
			event.preventDefault();
			onremove();
		}}
	>
		<p class="break-words">{$t('securitykeys.removeText', { name: key?.name ?? '' })}</p>
		<Input
			id="security-key-remove-password"
			type="password"
			label={$t('securitykeys.passwordLabel')}
			bind:value={password}
			autocomplete="current-password"
			required
			disabled={busy}
		/>
		{#if error}
			<Alert variant="error">{error}</Alert>
		{/if}
	</form>

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={onclose}>{$t('common.cancel')}</Button>
		<Button
			type="submit"
			form="security-key-remove"
			variant="danger"
			class="flex-1"
			loading={busy}
			disabled={!password}
		>
			{$t('securitykeys.removeButton')}
		</Button>
	{/snippet}
</Modal>
