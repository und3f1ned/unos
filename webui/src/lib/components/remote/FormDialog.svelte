<script lang="ts">
	// A dialog of Remote access with a form: its fields, the server's refusal under them, Cancel and
	// the submit button of the form, which both wait while the change runs.
	import type { Snippet } from 'svelte';
	import { Alert, Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		open: boolean;
		title: string;
		// The form's id, which the submit button of the footer names.
		formId: string;
		busy: boolean;
		// Why the server refused the last submit; "" for none.
		error: string;
		submitLabel: string;
		submitDisabled: boolean;
		oncancel: () => void;
		onsubmit: () => void;
		children: Snippet;
	};

	let { open, title, formId, busy, error, submitLabel, submitDisabled, oncancel, onsubmit, children }: Props = $props();
</script>

<Modal {open} {title} closable={!busy} onclose={oncancel}>
	<form
		id={formId}
		class="space-y-4"
		onsubmit={(e) => {
			e.preventDefault();
			onsubmit();
		}}
	>
		{@render children()}
		{#if error}
			<Alert variant="error"><p class="break-words">{error}</p></Alert>
		{/if}
	</form>
	{#snippet footer()}
		<Button variant="secondary" type="button" disabled={busy} onclick={oncancel}>{$t('common.cancel')}</Button>
		<Button variant="primary" type="submit" form={formId} loading={busy} disabled={submitDisabled}>{submitLabel}</Button
		>
	{/snippet}
</Modal>
