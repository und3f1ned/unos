<script lang="ts">
	import type { ApiToken } from '$lib/api/auth';
	import { Alert, Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// Asks before an API token is deleted: a program that uses it can no longer sign in.

	type Props = {
		/** The token to delete; the dialog is closed without one. */
		token: ApiToken | null;
		/** A token is being changed: the dialog waits and cannot be closed. */
		busy: boolean;
		/** Why it could not be deleted; '' otherwise. */
		error: string;
		ondelete: () => void;
		onclose: () => void;
	};

	let { token, busy, error, ondelete, onclose }: Props = $props();
</script>

<Modal
	open={token !== null}
	title={$t('tokens.deleteDialog.title')}
	size="md"
	danger
	closable={!busy}
	{onclose}
>
	{#if token}
		<p class="break-words">{$t('tokens.deleteDialog.text', { name: token.name })}</p>
		{#if error}
			<Alert variant="error" title={$t('tokens.deleteDialog.failed')} class="mt-3">{error}</Alert>
		{/if}
	{/if}
	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={onclose}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={busy} onclick={ondelete}>{$t('tokens.delete')}</Button>
	{/snippet}
</Modal>
