<script lang="ts">
	// Delete asks first, and warns when the deletion touches the interface of the connection to
	// UNOS or of the default route: it then waits to be kept (ADR-0424).
	import type { VirtualInterface } from '$lib/api/network';
	import { Alert, Button, Modal } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		// The interface the dialog asks about; null while it is closed.
		deleting: VirtualInterface | null;
		// The interfaces that carry the connection to UNOS or the default route.
		protectedNames: Set<string>;
		revertSeconds: number;
		busy: boolean;
		oncancel: () => void;
		onconfirm: () => void;
	};

	let { deleting, protectedNames, revertSeconds, busy, oncancel, onconfirm }: Props = $props();

	let deletionTouches = $derived(
		deleting
			? [deleting.name, ...deleting.ports, ...(deleting.parent ? [deleting.parent] : [])].filter((n) =>
					protectedNames.has(n)
				)
			: []
	);
</script>

<Modal
	open={deleting !== null}
	title={deleting ? $t('virtual.deleteTitle', { name: deleting.name }) : ''}
	size="md"
	danger
	closable={!busy}
	onclose={oncancel}
>
	{#if deleting}
		<p>{$t('virtual.deleteText', { name: deleting.name })}</p>
		{#if deletionTouches.length}
			<Alert variant="warning" class="mt-3">
				{$t('virtual.waitsWarning', { names: deletionTouches.join(', '), n: revertSeconds })}
			</Alert>
		{/if}
	{/if}
	{#snippet footer()}
		<Button variant="secondary" class="flex-1" disabled={busy} onclick={oncancel}>
			{$t('common.cancel')}
		</Button>
		<Button variant="danger" class="flex-1" loading={busy} onclick={onconfirm}>{$t('virtual.delete')}</Button>
	{/snippet}
</Modal>
