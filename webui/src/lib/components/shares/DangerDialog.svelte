<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The question before a change that cannot be undone (a deletion, a restart): Cancel and the
	// dangerous button. What it says is the caller's.
	type Props = {
		open: boolean;
		title: string;
		/** The request runs: the dialog cannot be closed, and the button waits for the answer. */
		busy: boolean;
		confirmLabel: string;
		confirmDisabled?: boolean;
		// Escape, the backdrop, the close button or Cancel, while the dialog is not busy.
		onclose: () => void;
		onconfirm: () => void;
		children: Snippet;
	};

	let { open, title, busy, confirmLabel, confirmDisabled = false, onclose, onconfirm, children }: Props =
		$props();
</script>

<Modal {open} {title} size="md" danger closable={!busy} {onclose}>
	{@render children()}

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={() => onclose()}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={busy} disabled={confirmDisabled} onclick={() => onconfirm()}>
			{confirmLabel}
		</Button>
	{/snippet}
</Modal>
