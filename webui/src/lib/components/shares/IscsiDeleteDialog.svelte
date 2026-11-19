<script lang="ts">
	import type { IscsiTarget } from '$lib/api/iscsi';
	import { Alert, Input } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import DangerDialog from './DangerDialog.svelte';
	import { messageOf } from './errors';

	// The deletion of a target and its LUN file, confirmed with the target's name. Mounted while open.
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

	let typed = $state('');
	let deleteError = $state('');

	/** Empties the name typed: Delete was pressed again behind the dialog. */
	export function reset() {
		typed = '';
		deleteError = '';
	}

	async function remove() {
		if (saving || typed !== target.name) return;
		deleteError = '';
		try {
			await onremove(typed);
		} catch (e) {
			deleteError = messageOf(e);
		}
	}
</script>

<DangerDialog
	open
	title={$t('iscsi.remove.title', { name: target.name })}
	busy={saving}
	confirmLabel={$t('iscsi.remove.confirm')}
	confirmDisabled={typed !== target.name}
	{onclose}
	onconfirm={remove}
>
	<form
		onsubmit={(event) => {
			event.preventDefault();
			remove();
		}}
		class="space-y-4"
	>
		<p class="text-sm break-words">{$t('iscsi.remove.warning', { path: target.path })}</p>
		{#if target.sessions.length > 0}
			<Alert variant="warning">
				{$t('iscsi.remove.sessions', { initiators: target.sessions.join(', ') })}
			</Alert>
		{/if}
		<Input
			id="iscsi-delete-confirm"
			label={$t('iscsi.remove.type', { name: target.name })}
			bind:value={typed}
			autocomplete="off"
			spellcheck={false}
		/>
	</form>
	{#if deleteError}
		<Alert variant="error" title={$t('iscsi.remove.notDeleted')}>{deleteError}</Alert>
	{/if}
</DangerDialog>
