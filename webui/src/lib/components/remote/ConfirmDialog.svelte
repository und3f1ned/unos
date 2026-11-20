<script lang="ts">
	// A dialog of Remote access that asks before a change: its text, Cancel, and the action, which
	// waits while another change runs.
	import type { Snippet } from 'svelte';
	import { Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		open: boolean;
		title: string;
		// A change that cuts something off: the dialog and its action are drawn as dangerous.
		danger?: boolean;
		confirmLabel: string;
		busy: boolean;
		oncancel: () => void;
		onconfirm: () => void;
		children: Snippet;
	};

	let { open, title, danger = false, confirmLabel, busy, oncancel, onconfirm, children }: Props = $props();
</script>

<Modal {open} {title} {danger} onclose={oncancel}>
	{@render children()}
	{#snippet footer()}
		<Button variant="secondary" onclick={oncancel}>{$t('common.cancel')}</Button>
		<Button variant={danger ? 'danger' : 'primary'} disabled={busy} onclick={onconfirm}>{confirmLabel}</Button>
	{/snippet}
</Modal>
