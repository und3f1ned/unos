<script lang="ts">
	import type { IscsiTarget } from '$lib/api/iscsi';
	import { Alert, ConfirmDialog } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { messageOf } from './errors';

	// The deletion of a target and its LUN file, confirmed with the target's name typed into an
	// empty field. Mounted while open.
	type Props = {
		target: IscsiTarget;
		/** A request of the tab runs: the dialog waits for it. */
		saving: boolean;
		/** Escape, the backdrop or Cancel, while nothing runs. */
		onclose: () => void;
		/** Deletes the target with the name typed; rejects with the server's error, which the dialog shows. */
		onremove: (typed: string) => Promise<void>;
	};

	let { target, saving, onclose, onremove }: Props = $props();

	let deleteError = $state('');

	/** Forgets the last refusal: Delete was pressed again behind the dialog. */
	export function reset() {
		deleteError = '';
	}

	// The dialog confirms only once the name typed matches it.
	async function remove() {
		if (saving) return;
		deleteError = '';
		try {
			await onremove(target.name);
		} catch (e) {
			deleteError = messageOf(e);
		}
	}
</script>

<ConfirmDialog
	open
	title={$t('iscsi.remove.title', { name: target.name })}
	confirmLabel={$t('iscsi.remove.confirm')}
	typeToConfirm={target.name}
	typeLabel={$t('iscsi.remove.type', { name: target.name })}
	typeExact
	busy={saving}
	oncancel={onclose}
	onconfirm={remove}
>
	<p class="text-sm break-words">{$t('iscsi.remove.warning', { path: target.path })}</p>
	{#if target.sessions.length > 0}
		<Alert variant="warning">
			{$t('iscsi.remove.sessions', { initiators: target.sessions.join(', ') })}
		</Alert>
	{/if}
	{#if deleteError}
		<Alert variant="error" title={$t('iscsi.remove.notDeleted')}>{deleteError}</Alert>
	{/if}
</ConfirmDialog>
